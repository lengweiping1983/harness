import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const row={id:'hook',root:'/fixture',session_id:'fixture',call_id:'call',tool:'fixture_tool',label:'Command 原文',reason:'',event:'PreToolUse',status:'failed',turn:1,step:1,source_seq:1,duration_ms:12,started:1,finished:2}
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {settleHookActivity} from '/src/lib/hookActivity.ts'
import {HookActivityRecords,HookActivityList} from '/src/components/chat/blocks/HookActivity.tsx'
import {Composer} from '/src/components/chat/Composer.tsx'
import {ProjectSidebar} from '/src/components/sidebar/ProjectSidebar.tsx'
import {Toaster} from '/src/components/overlays/CommandPalette.tsx'
import {useAppStore} from '/src/store/app.ts'
import {useDraftStore} from '/src/store/drafts.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
await setLocale('en');window.locale=setLocale;window.copy=t;window.draftWrites=0
const view=new URLSearchParams(location.search).get('view'),row=${JSON.stringify(row)}
useAppStore.setState({activeSessionId:'fixture',sessions:[{id:'fixture',title:'Conversation 原文',cwd:'/fixture'}],workspaces:[],toasts:[]})
if(view==='corrupt'){useDraftStore.getState().setText('fixture','User draft 原文');await Promise.resolve()}
if(view==='storage'){
 const original=Storage.prototype.setItem
 Storage.prototype.setItem=function(key,value){if(key==='harness.drafts.v1'){window.draftWrites++;throw Error('Storage unavailable')}return original.call(this,key,value)}
 useDraftStore.getState().setText('fixture','User draft 原文')
 await Promise.resolve()
}
const noop=()=>{}
const content=view==='empty'?React.createElement(HookActivityList,{rows:[],sessionId:'fixture'}):view==='passed'?React.createElement(HookActivityList,{rows:[{...row,status:'passed'}],sessionId:'fixture'}):['storage','corrupt'].includes(view)?React.createElement(Composer,{running:false,queued:0,planMode:false,connection:'connected',onSend:()=>{throw Error('Unexpected submit')},onCancel:noop,onTogglePlan:noop}):view==='sidebar'?React.createElement(React.Fragment,null,React.createElement(ProjectSidebar),React.createElement(Toaster)):React.createElement(HookActivityRecords,{rows:view==='settled'?settleHookActivity([{...row,status:'running'}]):[row],sessionId:'fixture'})
createRoot(document.getElementById('root')).render(content)
`
await browserScenario(async({server,browser})=>{
 for(const view of ['error','identity','output','empty','passed','storage','corrupt','sidebar','settled']){
  const page=await browser.newPage();page.setDefaultTimeout(15000)
  await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
  const requests=[],errors=[];page.on('pageerror',error=>errors.push(error.message))
  await page.route('**/api/v1/**',route=>{
   requests.push({method:route.request().method(),url:route.request().url()})
   if(view==='identity'||view==='output')return route.fulfill({json:{hook:{...row,root:view==='identity'?'/foreign':'/fixture',command:'echo Original 原文',stdout:'Output 原文',stderr:'',stdout_dropped:11,stderr_dropped:13}}})
   return route.fulfill({status:200,body:'broken',contentType:'application/json'})
  })
  if(view==='corrupt')await page.addInitScript(()=>{
   localStorage.setItem('harness.drafts.v1','Original damaged drafts')
   const original=Storage.prototype.setItem
   Storage.prototype.setItem=function(key,value){
    if(key.startsWith('harness.drafts.v1.invalid.'))throw Error('Fixture isolation unavailable')
    return original.call(this,key,value)
   }
  })
  await page.goto(server.resolvedUrls.local[0]+'__hook-draft-feedback?view='+view)
  if(['error','identity','output'].includes(view))await page.locator('[data-hook-run-id] > button').click()
  if(view==='sidebar'){
   await page.getByRole('button',{name:'Conversation 原文',exact:true}).click({button:'right'})
   await page.getByRole('menuitem',{name:await page.evaluate(()=>window.copy('ui.components.sidebar.ProjectSidebar.90d990618b19')),exact:true}).click()
  }
  const expected=()=>page.evaluate(view=>view==='settled'?window.copy('ui.lib.hookActivity.c28f6401fb6d'):view==='identity'?window.copy('ui.components.chat.blocks.HookActivity.04285ea11186'):view==='output'?window.copy('ui.hooks.outputDropped',{stdout:11,stderr:13}):view==='corrupt'?window.copy('ui.drafts.invalidStorage'):view==='storage'?window.copy('ui.store.drafts.f9e99a9805ac'):view==='passed'?window.copy('ui.hooks.groupPassed',{phase:window.copy('ui.components.chat.blocks.HookActivity.926b04e8ced4'),count:1}):window.copy('ui.http.invalidResponse'),view)
  const wait=async()=>{if(view==='empty')return;await page.waitForFunction(expected=>document.body.textContent.includes(expected),await expected())}
  await wait();const before=requests.length,writes=await page.evaluate(()=>window.draftWrites)
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(async({locale,theme})=>{await window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
   await wait();assert.equal(requests.length,before,'locale must not repeat operations')
   if(['storage','corrupt'].includes(view)){assert.equal(await page.locator('textarea').inputValue(),'User draft 原文');assert.equal(await page.evaluate(()=>window.draftWrites),writes)}
   if(view==='output')assert.ok((await page.locator('body').textContent()).includes('echo Original 原文'))
   if(view==='empty')assert.equal((await page.locator('#root').innerText()).trim(),'')
  }
  if(view==='corrupt')assert.equal(await page.evaluate(()=>localStorage.getItem('harness.drafts.v1')),'Original damaged drafts')
  assert.deepEqual(errors,[]);await page.close();console.log(view+': current-language feedback, original content and operation ownership preserved')
 }
},{entry:'/__hook-draft-feedback-entry.js',vite:{plugins:[{
 name:'hook-draft-feedback-fixture',resolveId(id){if(id==='/__hook-draft-feedback-entry.js')return id},load(id){if(id==='/__hook-draft-feedback-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__hook-draft-feedback',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__hook-draft-feedback','<html><body><div id="root"></div><script type="module" src="/__hook-draft-feedback-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})

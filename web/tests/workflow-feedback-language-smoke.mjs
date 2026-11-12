import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const tool=name=>({call_id:name,name,effect:'read',idempotent:true,status:'reported_success',source_seq:1,arguments:'{}',result:'Original tool result 原文',result_truncated:false})
const preview={run_id:'original',session_id:'fixture',status:'failed',turn:1,task:'Original task 原文',task_truncated:false,error:'Original diagnostic 原文',scope_known:true,version:'first',tools:[tool('fixture-first')],total_tools:2,uncertain_operations:0,verification_required:false,next_offset:1,continuation_text:'Continue 原文',inspection_text:'Inspect 原文',known_continuations:{}}
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {MemoryPanel} from '/src/components/overlays/MemoryPanel.tsx'
import {ConversationSearch} from '/src/components/overlays/ConversationSearch.tsx'
import {RecoveryReview} from '/src/components/inspector/RecoveryReview.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t;await setLocale('en')
const view=new URLSearchParams(location.search).get('view')
useAppStore.setState({activeSessionId:'fixture',sessions:[{id:'fixture',cwd:'/root'}],conversationSearchOpen:view==='search'})
const noop=()=>{}
const content=view.startsWith('memory')?React.createElement(MemoryPanel):view==='search'?React.createElement(ConversationSearch):React.createElement(RecoveryReview,{sessionId:'fixture',record:{id:'original',session_id:'fixture',text:'Original task 原文',status:'failed',created_at:1},onClose:noop,onSubmitted:()=>{throw Error('Unexpected continuation')},onOriginal:noop})
createRoot(document.getElementById('root')).render(content)
`
await browserScenario(async({server,browser})=>{
 for(const view of ['memory-read','memory-json','memory-save','search','recovery-read','recovery-stale']){
  const page=await browser.newPage();page.setDefaultTimeout(15000)
  const requests=[],errors=[];page.on('pageerror',error=>errors.push(error.message))
  await page.route('**/api/v1/**',route=>{
   const request=route.request(),url=new URL(request.url());requests.push({method:request.method(),path:url.pathname})
   if(view.startsWith('memory')&&view!=='memory-read')return route.fulfill({status:request.method()==='POST'?201:200,json:request.method()==='POST'?{memory:{id:'saved'}}:{memories:[],path:'/root/memory.json'}})
   if(view==='recovery-stale')return route.fulfill({json:{...preview,...(url.searchParams.has('offset')?{version:'second',tools:[tool('fixture-second')],next_offset:null}:{})}})
   return route.fulfill({status:200,body:'not json',contentType:'application/json'})
  })
  await page.goto(server.resolvedUrls.local[0]+'__workflow-feedback?view='+view)
  if(view==='memory-json')await page.locator('input[type=file]').setInputFiles({name:'invalid.json',mimeType:'application/json',buffer:Buffer.from('not json')})
  if(view==='memory-save'){
   await page.getByRole('textbox',{name:await page.evaluate(()=>window.copy('ui.memory.content')),exact:true}).fill('Original memory 原文')
   await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.memory.save')),exact:true}).click()
  }
  if(view==='search')await page.locator('[data-conversation-search]').fill('Original query 原文')
  if(view==='recovery-stale')await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.showMore')),exact:true}).click()
  const expected=()=>page.evaluate(view=>window.copy(view==='memory-json'?'ui.memory.invalidImportJson':view==='memory-save'?'ui.memory.saved':view==='recovery-stale'?'ui.components.inspector.RecoveryReview.ad2c90cbaebc':'ui.http.invalidResponse'),view)
  const wait=async()=>{
   if(view==='memory-save')await page.getByText(await expected(),{exact:true}).waitFor()
   else await page.waitForFunction(text=>Array.from(document.querySelectorAll('[role=alert]')).some(element=>element.textContent.startsWith(text)),await expected())
  }
  await wait();const before=requests.length
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(async({locale,theme})=>{await window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
   await wait();assert.equal(requests.length,before,view+': changing language repeated an operation')
   if(view==='search')assert.equal(await page.locator('[data-conversation-search]').inputValue(),'Original query 原文')
   if(view==='recovery-stale'){
    await page.getByText('fixture-first',{exact:true}).waitFor()
    assert.equal(await page.getByText('fixture-second',{exact:true}).count(),0)
   }
  }
  if(view==='memory-json')assert.equal(requests.filter(request=>request.method==='POST').length,0)
  if(view==='memory-save')assert.equal(requests.filter(request=>request.method==='POST').length,1)
  if(view.startsWith('recovery'))assert.equal(requests.filter(request=>request.method==='POST').length,0)
  assert.deepEqual(errors,[],view);await page.close();console.log(view+': feedback follows language, input and operation counts are preserved')
 }
},{entry:'/__workflow-feedback-entry.js',vite:{plugins:[{
 name:'workflow-feedback-language-fixture',resolveId(id){if(id==='/__workflow-feedback-entry.js')return id},load(id){if(id==='/__workflow-feedback-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__workflow-feedback',async(_req,res,next)=>{
  try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__workflow-feedback','<html><body><div id="root"></div><script type="module" src="/__workflow-feedback-entry.js"></script></body></html>'))}catch(error){next(error)}
 })},
}]}})

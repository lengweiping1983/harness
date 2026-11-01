import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {browserScenario} from './browser-scenario.mjs'
const en=JSON.parse(await readFile(new URL('../src/i18n/locales/en.json',import.meta.url)))
const preview={run_id:'original',session_id:'fixture',status:'failed',turn:1,task:'Original task 原文',task_truncated:false,error:'Original diagnostic 原文',scope_known:true,version:'first',tools:[{call_id:'read',name:'fixture',effect:'read',idempotent:true,status:'reported_success',source_seq:1,arguments:'{}',result:'Original result 原文',result_truncated:false}],total_tools:2,uncertain_operations:0,verification_required:false,next_offset:1,continuation_text:'Continue 原文',inspection_text:'Inspect 原文',known_continuations:{}}
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {RecoveryReview} from '/src/components/inspector/RecoveryReview.tsx'
import {saveRecoveryDraft,readRecoveryDraft} from '/src/lib/recoveryDraft.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;window.accepted=[];window.storageReads=0
const getItem=Storage.prototype.getItem
Storage.prototype.getItem=function(key){if(key.startsWith('harness.recovery-draft.'))window.storageReads++;return getItem.call(this,key)}
saveRecoveryDraft('fixture','original',{mode:'inspect',instructions:{inspect:'Inspect 原文'},note:'',sourceVersion:'first'})
window.draft=()=>readRecoveryDraft('fixture','original')
window.replaceDraft=()=>saveRecoveryDraft('fixture','original',{mode:'inspect',instructions:{inspect:'New draft 原文'},note:'',sourceVersion:'first'})
const root=createRoot(document.getElementById('root'))
window.mount=shown=>root.render(shown?React.createElement(RecoveryReview,{sessionId:'fixture',record:{id:'original',session_id:'fixture',text:'Original task 原文',status:'failed',created_at:1},onClose:()=>{},onSubmitted:run=>window.accepted.push(run.id),onOriginal:()=>{}}):null)
window.mount(true)
`
await browserScenario(async({server,browser})=>{
 for(const scenario of ['pagination','accepted-write','accepted-write-new-draft']){
  const page=await browser.newPage();page.setDefaultTimeout(15000)
  const errors=[];page.on('pageerror',error=>errors.push(error.message))
  let release,entered,finished,reads=0,writes=0,pages=0
  const held=new Promise(resolve=>{entered=resolve}),done=new Promise(resolve=>{finished=resolve})
  let aborted=false
  page.on('requestfailed',request=>{if(new URL(request.url()).searchParams.has('offset'))aborted=true})
  await page.route('**/api/v1/**',async route=>{
   const request=route.request(),url=new URL(request.url())
   if(url.pathname.endsWith('/recovery')){
    if(url.searchParams.has('offset')){
     pages++;entered();await new Promise(resolve=>{release=resolve})
     try{await route.fulfill({json:{...preview,version:'obsolete',next_offset:null}})}catch(error){assert.equal(aborted,true,'only an observed cancelled read may ignore delivery failure')}
     finished();return
    }
    reads++;return route.fulfill({json:{...preview,task:reads>1?'Fresh task 原文':preview.task}})
   }
   if(url.pathname.includes('/submissions/'))return route.fulfill({json:{run:null}})
   if(url.pathname.endsWith('/continue')){
    writes++
    if(scenario==='pagination')return route.fulfill({status:403,json:{code:'fixture/refusal',error:'Original refusal'}})
    const body=request.postDataJSON();entered();await new Promise(resolve=>{release=resolve})
    const run={id:'accepted',session_id:'fixture',retry_of:'original',status:'queued',created_at:2,recovery:{mode:body.mode,source_version:body.recovery_version,verification_note:body.verification_note,instruction_text:body.text},text:'Opaque assembled prompt 原文'}
    await route.fulfill({json:run});finished();return
   }
   throw Error('Unexpected recovery request '+url)
  })
  await page.goto(server.resolvedUrls.local[0]+'__recovery-lifecycle')
  await page.locator('summary').filter({hasText:'fixture'}).waitFor()
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900})
   await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme},{locale,theme})
   const status=await page.evaluate(()=>window.copy('ui.components.inspector.RecoveryReview.7263ac57a044'))
   await page.locator('summary').filter({hasText:status}).waitFor()
   assert.equal(reads,1)
  }
  const submit=page.getByRole('button',{name:en['ui.components.inspector.RecoveryReview.11a3ff2c9eaf'],exact:true})
  await submit.click()
  if(scenario==='pagination'){
   await page.getByText('Original refusal',{exact:true}).waitFor()
   await page.getByRole('button',{name:en['ui.showMore'],exact:true}).evaluate(button=>{button.click();button.click()})
   await held;assert.equal(pages,1)
   const cancelled=page.waitForEvent('requestfailed',{predicate:request=>new URL(request.url()).searchParams.has('offset')})
   await page.getByRole('button',{name:en['ui.components.inspector.RecoveryReview.63ca2f5e52a1'],exact:true}).click()
   await cancelled;await page.getByText('Fresh task 原文',{exact:true}).waitFor()
   release();await done
   assert.equal(await page.getByRole('alert').count(),0)
   assert.equal(await page.getByText('Fresh task 原文',{exact:true}).isVisible(),true)
  }else{
   await held
   await page.evaluate(()=>window.mount(false))
   await submit.waitFor({state:'detached'})
   if(scenario==='accepted-write-new-draft')await page.evaluate(()=>window.replaceDraft())
   const baseline=await page.evaluate(()=>window.storageReads)
   release();await done
   await page.waitForFunction(baseline=>window.storageReads>baseline,baseline)
   if(scenario==='accepted-write-new-draft')assert.equal(await page.evaluate(()=>window.draft()?.instructions.inspect),'New draft 原文')
   else assert.equal(await page.evaluate(()=>window.draft()),null)
   assert.deepEqual(await page.evaluate(()=>window.accepted),[])
  }
  assert.equal(writes,1);assert.deepEqual(errors,[])
  await page.close();console.log(scenario+': stale UI results are isolated; the accepted write is not replayed or cancelled')
 }
},{entry:'/__recovery-lifecycle-entry.js',vite:{plugins:[{
 name:'recovery-lifecycle-fixture',resolveId(id){if(id==='/__recovery-lifecycle-entry.js')return id},load(id){if(id==='/__recovery-lifecycle-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__recovery-lifecycle',async(_req,res,next)=>{
  try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__recovery-lifecycle','<html><body><div id="root"></div><script type="module" src="/__recovery-lifecycle-entry.js"></script></body></html>'))}catch(error){next(error)}
 })},
}]}})

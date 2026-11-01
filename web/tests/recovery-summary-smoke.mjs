import assert from 'node:assert/strict'
import {execFileSync} from 'node:child_process'
import {fileURLToPath} from 'node:url'
import {browserScenario} from './browser-scenario.mjs'
import {fixturePython} from './python-fixture.mjs'

const snapshots=JSON.parse(execFileSync(fixturePython,['-c',`
import json
from tests.test_run_recovery import RunRecoveryTests
from harness.app.runtime import RunController
from harness.app.recovery import recovery_preview
from harness.core.runs import AgentRun
from harness.session.events import SessionEvent
fixture=RunRecoveryTests();fixture.setUp()
try:
 fixture.save(*(AgentRun(id=identity,session_id='session',status='failed',text='Original task 原文') for identity in ('original','unscoped','empty','single')))
 fixture.host.runtime=RunController(fixture.host)
 events=[SessionEvent('turn/start',0,1,{'turn':1,'run_id':'original'})]
 cases=[('read','reported_success'),('read','reported_success'),('workspace_write','reported_success'),('external_write','reported_error'),('external_write','unknown'),('execute','not_executed'),('unknown','reported_success')]
 for index,(effect,status) in enumerate(cases):
  events.append(SessionEvent('tool/call',len(events),index+2,{'call_id':'reused','step':index+1,'status':'running','name':'tool-'+str(index),'effect':effect,'input':{},'idempotent':False}))
  if status!='unknown':
   events.append(SessionEvent('tool/result',len(events),index+2,{'call_id':'reused','step':index+1,'name':'tool-'+str(index),'results':[{'tool_use_id':'reused','is_error':status=='reported_error','content':'Original receipt 原文'}],**({'display':{'not_executed':True}} if status=='not_executed' else {})}))
 events.append(SessionEvent('turn/end',len(events),10,{'turn':1,'run_id':'original'}))
 fixture.session.events=events
 first=recovery_preview(fixture.host,'session','original',limit=1)
 tail=recovery_preview(fixture.host,'session','original',offset=1)
 unscoped=recovery_preview(fixture.host,'session','unscoped')
 fixture.session.events=[SessionEvent('turn/start',0,1,{'turn':1,'run_id':'empty'}),SessionEvent('turn/end',1,2,{'turn':1,'run_id':'empty'})]
 empty=recovery_preview(fixture.host,'session','empty')
 fixture.session.events=[SessionEvent('turn/start',0,1,{'turn':1,'run_id':'single'}),SessionEvent('tool/call',1,2,{'call_id':'one','name':'single-write','status':'running','effect':'external_write'}),SessionEvent('turn/end',2,3,{'turn':1,'run_id':'single'})]
 single=recovery_preview(fixture.host,'session','single')
 assert first['uncertain_operations']==tail['uncertain_operations']==2
 assert len(first['tools'])==1 and first['tools'][0]['effect']=='read'
 assert single['uncertain_operations']==single['total_tools']==1
 print(json.dumps(dict(first=first,tail=tail,unscoped=unscoped,empty=empty,single=single),ensure_ascii=False))
finally:fixture.doCleanups()
`],{cwd:fileURLToPath(new URL('../../',import.meta.url)),encoding:'utf8'}))

const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {RecoveryReview} from '/src/components/inspector/RecoveryReview.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t
const scenario=new URLSearchParams(location.search).get('scenario'),id=['unscoped','empty','single'].includes(scenario)?scenario:'original'
createRoot(document.getElementById('root')).render(<RecoveryReview sessionId="session" record={{id,session_id:'session',text:'Original task 原文',status:'failed',created_at:1}} onClose={()=>{}} onSubmitted={()=>{throw Error('Rejected continuation cannot be accepted')}} onOriginal={()=>{}}/>)
`

await browserScenario(async({server,browser})=>{
 for(const scenario of ['paged','version-change','metadata-change','unscoped','empty','single']){
  const page=await browser.newPage(), errors=[]
  page.on('pageerror',error=>errors.push(error.message))
  let reads=0,pages=0,writes=0
  await page.route('**/api/v1/**',route=>{
   const request=route.request(),url=new URL(request.url())
   if(url.pathname.endsWith('/recovery')){
    if(url.searchParams.has('offset')){
     ++pages
     return route.fulfill({json:scenario==='version-change'?{...snapshots.tail,version:'changed'}:scenario==='metadata-change'?{...snapshots.tail,uncertain_operations:0,verification_required:false}:snapshots.tail})
    }
    ++reads
    return route.fulfill({json:snapshots[scenario]??{...snapshots.first,...(reads>1&&scenario==='version-change'?{version:'changed'}:{})}})
   }
   if(url.pathname.includes('/submissions/'))return route.fulfill({json:{run:null}})
   if(url.pathname.endsWith('/continue')){++writes;return route.fulfill({status:403,json:{code:'fixture/refused',error:'Original refusal 原文'}})}
   throw Error('Unexpected recovery request '+url)
  })
  await page.goto(server.resolvedUrls.local[0]+'__recovery-summary?scenario='+scenario)
  const copy=(key,params)=>page.evaluate(({key,params})=>window.copy(key,params),{key,params})
  const overview=page.locator('[data-recovery-summary]')
  const count=scenario==='single'?1:2
  const overviewText=scenario==='unscoped'?'ui.recovery.scopeUnknown':scenario==='empty'?'ui.recovery.noPendingOperations':'ui.recovery.pendingOperations'
  await overview.getByText(await copy(overviewText,{count}),{exact:true}).waitFor()
  for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440])for(const font of [10,22]){
   await page.setViewportSize({width,height:1000})
   await page.evaluate(({locale,dark,font})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark);document.documentElement.style.fontSize=font+'px'},{locale,dark,font})
   await overview.getByText(await copy(overviewText,{count}),{exact:true}).waitFor()
   await overview.getByText(await copy('ui.recovery.evidenceLimit'),{exact:true}).waitFor()
   if(!['unscoped','empty'].includes(scenario))await overview.getByText(await copy('ui.recovery.loadedRecords',{loaded:1,total:scenario==='single'?1:7}),{exact:true}).waitFor()
   else assert.equal(await overview.getByText(await copy('ui.recovery.loadedRecords',{loaded:0,total:0}),{exact:true}).count(),0)
   assert.equal(reads,1);assert.equal(pages,0);assert.equal(writes,0)
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
  }
  if(scenario==='paged'){
   if(process.env.HARNESS_TEST_SCREENSHOT){await page.setViewportSize({width:390,height:1000});await overview.scrollIntoViewIfNeeded();await page.screenshot({path:process.env.HARNESS_TEST_SCREENSHOT})}
   assert.equal(await page.getByText('tool-4',{exact:true}).count(),0,'Unknown write belongs to a later page')
   await page.getByRole('button',{name:await copy('ui.showMore'),exact:true}).click()
   await overview.getByText(await copy('ui.recovery.loadedRecords',{loaded:7,total:7}),{exact:true}).waitFor()
   await overview.getByText(await copy('ui.recovery.pendingOperations',{count:2}),{exact:true}).waitFor()
   assert.equal(await page.getByRole('button',{name:await copy('ui.showMore'),exact:true}).count(),0)
   assert.equal(writes,0);assert.equal(pages,1)
  }else{
   await page.getByRole('radio').nth(1).check()
   const submit=page.getByRole('button',{name:await copy('ui.components.inspector.RecoveryReview.0ae5c3b7ccd1'),exact:true})
   if(scenario==='empty')assert.equal(await submit.isEnabled(),true)
   else{
    assert.equal(await submit.isEnabled(),false)
    await page.locator('#recovery-instruction').fill('New instruction 原文')
    await page.locator('#recovery-note').fill('Manually checked original operations 原文')
    await page.getByRole('switch').check()
    assert.equal(await submit.isEnabled(),true)
    if(['version-change','metadata-change'].includes(scenario)){
     await page.getByRole('button',{name:await copy('ui.showMore'),exact:true}).click()
     await page.getByRole('alert').getByText(await copy(scenario==='version-change'?'ui.components.inspector.RecoveryReview.ad2c90cbaebc':'ui.http.invalidResponse'),{exact:true}).waitFor()
     assert.equal(await submit.isEnabled(),false);assert.equal(await page.getByRole('switch').isChecked(),false)
     await overview.getByText(await copy('ui.recovery.pendingOperations',{count:2}),{exact:true}).waitFor()
     await page.getByRole('button',{name:await copy('ui.components.inspector.RecoveryReview.a4003263a248'),exact:true}).click()
     await page.locator('#recovery-note').waitFor()
     assert.equal(await page.locator('#recovery-instruction').inputValue(),'New instruction 原文')
     assert.equal(await page.locator('#recovery-note').inputValue(),'Manually checked original operations 原文')
     assert.equal(await page.getByRole('switch').isChecked(),false);assert.equal(await submit.isEnabled(),false)
     assert.equal(writes,0)
     await page.getByRole('switch').check();assert.equal(await submit.isEnabled(),true)
    }else{
     assert.equal(await overview.getByText(await copy('ui.recovery.noPendingOperations'),{exact:true}).count(),0)
    }
    await submit.click()
    await page.getByText('Original refusal 原文',{exact:true}).waitFor()
    assert.equal(writes,1)
   }
  }
  assert.deepEqual(errors,[]);await page.close()
  console.log(scenario+': actual full-run evidence stays visible and consistent; stale confirmation is revoked without losing drafts or repeating writes')
 }
},{entry:'/__recovery-summary-entry.jsx',vite:{plugins:[{
 name:'recovery-summary-fixture',resolveId(id){if(id==='/__recovery-summary-entry.jsx')return id},load(id){if(id==='/__recovery-summary-entry.jsx')return entry},
 configureServer(server){server.middlewares.use('/__recovery-summary',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__recovery-summary','<html><body><div id="root"></div><script type="module" src="/__recovery-summary-entry.jsx"></script></body></html>'))}catch(error){next(error)}})},
}]}})

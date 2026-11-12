import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ContextAuditPanel} from '/src/components/inspector/ContextAuditPanel.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t;setLocale('en')
const mode=new URLSearchParams(location.search).get('mode')
createRoot(document.getElementById('root')).render(React.createElement(ContextAuditPanel,{sessionId:'fixture',agentId:mode.startsWith('child')?'child':undefined}))
`
await browserScenario(async({server,browser})=>{
 for(const mode of ['main-known','child-known','main-unreported','child-unreported']){
  const page=await browser.newPage(),errors=[],requests=[];let reads=0
  page.on('pageerror',error=>errors.push(error.message))
  await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
  await page.route('**/api/v1/**',route=>{
   assert.equal(route.request().method(),'GET');const path=new URL(route.request().url()).pathname;requests.push(path)
   if(path.includes('/context/compactions/'))return route.fulfill({json:{text:mode.endsWith('unreported')?'':'原文😀',offset:0,total_chars:mode.endsWith('unreported')?0:3,next_offset:null}})
   if(path.endsWith('/context/compact'))return route.fulfill({json:null})
   if(path==='/api/v1/settings')return route.fulfill({json:{config:{context_auto_manage:true,context_working_budget:10000,summary_max_tokens:null,output_reserve:1000,context_window:12000,tool_result_max_chars:null,tool_schema_mode:'auto'},effective:{context_window:12000}}})
   if(!path.endsWith('/context')){errors.push('Unexpected request: '+path);return route.fulfill({status:400,json:{error:'Unexpected fixture request'}})}
   reads++
   return route.fulfill({json:{session_id:'fixture',through:19,has_more:false,next_before:null,current:{through:19,event_count:1234,surface_nodes:12,message_count:5,roles:{UserMessage:2},latest_request:{id:'trace',model:'Original model 原文',time:1,status:'completed',message_count:3,tool_count:4,usage:mode.endsWith('unreported')?null:{input_tokens:5678}},latest_reported_request:{id:'reported',model:'Reported model 原文',time:2,status:'completed',usage:{input_tokens:4321}},statistics:{usage:{},requests:1,tool_calls:0,tool_errors:0,subagents:0,reported_requests:0,unreported_requests:1,compaction_requests:0,cache_inconsistent_requests:0}},compactions:[{id:7,time:1,status:'completed',turn:2,policy_version:3,before_approx_tokens:12345,after_approx_tokens:1234,summary_seq:7,total_chunks:3,completed_chunks:2,shadowed_count:5,retained_nodes:2,pruned_results:3,chars_removed:1234},{id:8,time:1,status:'completed',before_approx_tokens:30,shadowed_count:6,pruned_results:0,chars_removed:0},{id:9,time:1,status:'completed',turn:4,pruned_results:0,chars_removed:0},{id:10,time:1,status:'completed',policy_version:5,pruned_results:0,chars_removed:0}]}})
  })
  await page.goto(server.resolvedUrls.local[0]+'__audit-copy?mode='+mode)
  await page.getByText(await page.evaluate(()=>window.copy('ui.context.review.records')),{exact:true}).click()
  await page.getByText(await page.evaluate(()=>window.copy('ui.components.inspector.ContextAuditPanel.bc30ea799c52')),{exact:true}).click()
  assert.equal(await page.locator('[data-context-composition]').count(),0,'Input details must not be rendered')
  await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.components.inspector.ContextAuditPanel.bea0336e20d1')),exact:true}).click()
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
   const expected=await page.evaluate(({locale,mode})=>[
    window.copy('ui.context.audit.snapshot',{events:(1234).toLocaleString(locale),nodes:'12',through:19}),
    window.copy(mode.startsWith('child')?'ui.context.audit.latestChildRequest':'ui.context.audit.latestMainRequest'),
    window.copy(mode.endsWith('unreported')?'ui.context.audit.requestUnreported':'ui.context.audit.requestUsage',{tokens:(5678).toLocaleString(locale),messages:'3',tools:'4'}),
    window.copy('ui.context.audit.loaded',{count:4}),window.copy('ui.context.audit.chunks',{completed:2,total:3}),
    window.copy('ui.context.audit.replacedRetained',{count:5,retained:2}),window.copy('ui.context.audit.replaced',{count:6}),
    window.copy('ui.context.audit.pruned',{count:3,chars:(1234).toLocaleString(locale)}),
    window.copy('ui.context.audit.items',{count:'2'}),
    window.copy('ui.context.audit.reportedUsage',{tokens:(4321).toLocaleString(locale)}),
    window.copy('ui.context.audit.identityTurnPolicy',{id:7,turn:2,policy:3}),window.copy('ui.context.audit.identity',{id:8}),
    window.copy('ui.context.audit.identityTurn',{id:9,turn:4}),window.copy('ui.context.audit.identityPolicy',{id:10,policy:5}),
    window.copy('ui.context.audit.estimatedChange',{before:(12345).toLocaleString(locale),after:(1234).toLocaleString(locale)}),
    window.copy('ui.context.audit.estimated',{before:'30'}),
    window.copy('ui.context.audit.range',{start:mode.endsWith('unreported')?'0':'1',end:mode.endsWith('unreported')?'0':'3',total:mode.endsWith('unreported')?'0':'3'})],{locale,mode})
   for(const text of expected)await page.getByText(text,{exact:true}).waitFor()
   assert.equal(reads,1)
   assert.equal(await page.getByText(/^Original model 原文/).count(),1)
   assert.equal(await page.getByText(/^Reported model 原文/).count(),1)
   const original=page.locator('pre');await original.waitFor();assert.equal(await original.textContent(),mode.endsWith('unreported')?'':'原文😀')
  }
  assert.deepEqual(errors,[]);assert.equal(requests.length,mode.startsWith('child')?2:4);await page.close();console.log('Context audit complete bilingual copy without extra requests: '+mode)
 }
},{entry:'/__audit-copy-entry.js',vite:{plugins:[{name:'audit-copy-fixture',resolveId(id){if(id==='/__audit-copy-entry.js')return id},load(id){if(id==='/__audit-copy-entry.js')return entry},configureServer(server){server.middlewares.use('/__audit-copy',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__audit-copy','<html><body><div id="root"></div><script type="module" src="/__audit-copy-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})

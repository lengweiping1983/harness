import assert from 'node:assert/strict'
import {execFileSync} from 'node:child_process'
import {readFile} from 'node:fs/promises'
import {fixturePython} from './python-fixture.mjs'
import {browserScenario} from './browser-scenario.mjs'
const rows=JSON.parse(execFileSync(fixturePython,['-c',`
import json
from types import SimpleNamespace
from harness.web.execution_trace import project,page
from harness.core.runs import AgentRun,ACTIVE_STATES,TERMINAL_STATES
from harness.subagent.archive import subagent_summary
from harness.session.request_traces import RequestTraces
from harness.session.session import Session
agents=[subagent_summary(AgentRun(id='member-'+status,parent_session_id='owner',session_id='child-'+status,status=status,created_at=1,description='Original '+status)) for status in sorted(ACTIVE_STATES|TERMINAL_STATES)]
session=Session();archive=RequestTraces()
for status in ['started','completed','failed','interrupted']:
 identity=archive.start(session,body=json.dumps({'model':'Original model','messages':[]}),url='https://fixture.invalid',request_id=status,purpose='agent')
 if status!='started':archive.finish(identity,status)
traces=archive.page(session.id)['requests']
events=[]
for index,status in enumerate(['unknown','running','done','error','skipped']):
 call='tool-'+status
 events.append(SimpleNamespace(type='tool/call',seq=index*2,time=1,data={'call_id':call,'name':'Original '+status,'status':'done' if status=='done' else 'error' if status=='error' else 'running','turn':index}))
 if status=='unknown':events.append(SimpleNamespace(type='turn/end',seq=index*2+1,time=1,data={'turn':index}))
 if status=='skipped':events.append(SimpleNamespace(type='tool/result',seq=index*2+1,time=1,data={'display':{'steering_deferred':True},'results':[{'tool_use_id':call,'is_error':True,'content':'Original result'}]}))
print(json.dumps(page(project('owner',events,traces,agents,True),limit=100)['entries'],ensure_ascii=False))
`],{encoding:'utf8'})).map((row,index)=>({...row,time:(index+1)*3600000}))
const catalogs=Object.fromEntries(await Promise.all(['en','zh-CN'].map(async locale=>[locale,JSON.parse(await readFile(new URL('../../harness/i18n/locales/'+locale+'.json',import.meta.url),'utf8'))])))
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ExecutionTracePanel} from '/src/components/inspector/ExecutionTracePanel.tsx'
import {api} from '/src/lib/api.ts'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;setLocale('en');window.reads=0;window.parts=[]
api.executionTrace=async()=>{window.reads++;return {ok:true,data:{entries:${JSON.stringify(rows)},has_more:false,next_after:null,context_missing:false,session_id:'owner'}}}
api.executionTracePart=async(owner,id,part)=>{window.parts.push(part);const text='Original '+part+' 原文';return {ok:true,data:{text,offset:0,total_chars:Array.from(text).length,next_offset:null}}}
createRoot(document.getElementById('root')).render(React.createElement(ExecutionTracePanel,{sessionId:'owner'}))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage();page.setDefaultTimeout(5000)
 const errors=[];page.on('pageerror',error=>{errors.push(error.message);console.error(error.message)})
 await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
 await page.goto(server.resolvedUrls.local[0]+'__trace-resource')
 await page.waitForFunction(()=>window.reads===1)
 // 所有行分散到各自小时组，避免展开分页与动态键验证相互遮蔽。
 for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(async({locale,dark})=>{await window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  for(const row of rows){
   const statusKey={started:'ui.lib.requestTraces.cc1f354b0df6',completed:'ui.components.inspector.ContextAuditPanel.f28461bb49c8',failed:'ui.components.chat.Conversation.28384d7afd2e',interrupted:'ui.member.status.interrupted'}[row.status]??'ui.trace.status.'+row.status
   const status=catalogs[locale][statusKey];assert.ok(status,'Missing source key '+statusKey)
   const card=page.locator('[data-trace-id="'+row.id+'"]')
   await card.getByText(status,{exact:true}).waitFor()
   await card.getByText(row.name,{exact:true}).waitFor()
   await card.getByText(catalogs[locale]['ui.trace.'+(row.agent?'agent':row.kind)],{exact:true}).first().waitFor()
   if(row.agent)await card.getByRole('button',{name:catalogs[locale]['ui.trace.enterAgent']+' · '+row.agent.description+' · '+status,exact:true}).waitFor()
  }
  assert.equal(await page.evaluate(()=>window.reads),1)
 }
 const tool=page.locator('[data-trace-id="tool:2"]')
 await tool.locator('details > summary').click()
 await page.clock.runFor(100)
 await page.waitForFunction(()=>window.parts.length===1)
 for(const locale of ['en','zh-CN','en']){
  await page.evaluate(async locale=>await window.locale(locale),locale)
  for(const part of ['input','result'])await tool.getByRole('button',{name:catalogs[locale]['ui.trace.'+part],exact:true}).waitFor()
  assert.equal(await page.evaluate(()=>window.parts.length),1)
 }
 await tool.getByRole('button',{name:catalogs.en['ui.trace.result'],exact:true}).click()
 await page.waitForFunction(()=>window.parts.length===2)
 await tool.getByText('Original result 原文',{exact:true}).waitFor()
 assert.deepEqual(await page.evaluate(()=>window.parts),['input','result'])
 assert.deepEqual(errors,[])
 console.log('Execution trace: actual request archive, current Agent states and tool projection resolve finite type/status resource chains en/zh/en without refetching')
},{entry:'/__trace-resource-entry.js',vite:{plugins:[{name:'trace-resource-fixture',resolveId(id){if(id==='/__trace-resource-entry.js')return id},load(id){if(id==='/__trace-resource-entry.js')return entry},configureServer(server){server.middlewares.use('/__trace-resource',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__trace-resource','<!doctype html><html><body><div id="root"></div><script type="module" src="/__trace-resource-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})

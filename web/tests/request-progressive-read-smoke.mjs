import assert from 'node:assert/strict'
import {execFileSync} from 'node:child_process'
import {fixturePython} from './python-fixture.mjs'
import {readFileSync} from 'node:fs'
import {browserScenario} from './browser-scenario.mjs'
const projection=JSON.parse(execFileSync(fixturePython,['-c',`import json
from harness.llm.request_composition import request_composition
from harness.llm.prompt_cache import compare_prefix
old={'model':'old','messages':[{'role':'user','content':'Original input 原文'}],'tools':[],'temperature':0,'top_p':0.2}
new={**old,'model':'new','temperature':1,'top_p':0.7,'tools':[{'type':'function','function':{'name':'Original_tool','description':'Original description','parameters':{}}}]}
print(json.dumps({'composition':request_composition(new),'cache':compare_prefix(old,new,same_endpoint=False)},ensure_ascii=False))`],{cwd:new URL('../../',import.meta.url),encoding:'utf8'}))
const resources=Object.fromEntries(['en','zh-CN'].map(locale=>[locale,JSON.parse(readFileSync(new URL('../src/i18n/locales/'+locale+'.json',import.meta.url),'utf8'))]))
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {TraceDetail} from '/src/components/inspector/RequestTracePanel.tsx'
import {api} from '/src/lib/api.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.reads=[];window.copy=t;window.locale=setLocale
const detail=id=>({request:{id,session_id:'fixture',status:'completed',model:'Readable '+id,endpoint:'fixture',request_id:id,time:1,body_bytes:0},messages:[],parameters:{},tool_names:[],cache_comparison:{previous_id:'previous',...${JSON.stringify(projection.cache)}},composition:${JSON.stringify(projection.composition)}})
window.rejectSummary=false
api.requestTrace=(_s,id,signal,summary)=>summary&&window.rejectSummary?Promise.resolve({ok:false,status:400,code:'bad-request',message:'SERVER ORIGINAL',copy:{key:'ui.requestComposition.summaryRejected'}}):summary?Promise.resolve({ok:true,data:detail(id)}):new Promise(resolve=>window.reads.push({id,signal,resolve}))
api.requestTracePart=async()=>({ok:true,data:{text:'Original body',offset:0,total_chars:13,next_offset:null}})
const root=createRoot(document.getElementById('root'))
window.show=(id,active=true)=>{setLocale('en');root.render(React.createElement(TraceDetail,{sessionId:'fixture',traceId:id,active,onBack:()=>{},onSelect:()=>{}}))}
window.show('A')
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message))
 await page.goto(server.resolvedUrls.local[0]+'__trace-progress')
 await page.getByRole('heading',{name:'Readable A',exact:true}).waitFor().catch(async error=>{console.error(errors,await page.locator('body').innerText());throw error})
 await page.getByText('Original body',{exact:true}).waitFor()
 await page.waitForFunction(()=>window.reads.length===1)
 await page.getByText(await page.evaluate(()=>window.copy('ui.requestComposition.title')),{exact:true}).click()
 for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme},{locale,theme})
  await page.getByText('Original body',{exact:true}).waitFor();assert.equal(await page.evaluate(()=>window.reads.length),1)
  for(const row of projection.composition.groups){const key='ui.requestComposition.'+row.kind;assert.ok(resources[locale][key]);await page.getByRole('term').getByText(resources[locale][key],{exact:true}).waitFor()}
  for(const change of projection.cache.changes){const key='ui.requestStats.cacheChange.'+change;assert.ok(resources[locale][key]);const text=resources[locale][key].replace('{settings}',projection.cache.changed_settings.join(resources[locale]['ui.common.list_separator']));await page.getByText(text,{exact:true}).waitFor()}
 }
 await page.evaluate(()=>window.reads[0].resolve({ok:false,message:'Slow diagnostics failed'}))
 await page.getByRole('alert').filter({hasText:'Slow diagnostics failed'}).waitFor()
 await page.getByRole('heading',{name:'Readable A',exact:true}).waitFor().catch(async error=>{console.error(errors,await page.locator('body').innerText());throw error})
 await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.requestComposition.retry')),exact:true}).click()
 await page.waitForFunction(()=>window.reads.length===2)
 await page.evaluate(()=>window.show('B'))
 await page.waitForFunction(()=>window.reads[1].signal.aborted)
 await page.getByRole('heading',{name:'Readable B',exact:true}).waitFor()
 await page.evaluate(()=>window.reads[1].resolve({ok:false,message:'STALE FAILURE'}))
 assert.equal(await page.getByText('STALE FAILURE',{exact:true}).count(),0)
 await page.evaluate(()=>window.show('B',false))
 await page.waitForFunction(()=>window.reads.at(-1).signal.aborted)
 await page.evaluate(()=>{window.rejectSummary=true;window.show('C')})
 const readsBefore=await page.evaluate(()=>window.reads.length)
 for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme},{locale,theme})
  await page.getByRole('alert').filter({hasText:await page.evaluate(()=>window.copy('ui.requestComposition.summaryRejected'))}).waitFor()
  assert.equal(await page.getByRole('heading',{name:'Readable C',exact:true}).count(),0)
  assert.equal(await page.evaluate(()=>window.reads.length),readsBefore)
 }
 await page.evaluate(()=>{window.rejectSummary=false})
 await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.requestComposition.retry')),exact:true}).click()
 await page.getByRole('heading',{name:'Readable C',exact:true}).waitFor()
 await page.waitForFunction(count=>window.reads.length===count+1,readsBefore)
 assert.deepEqual(errors,[]);await page.close()
 console.log('Request detail: readable first screen, slow diagnostics failure retains content, retry and scope cancellation passed')
},{entry:'/__trace-progress-entry.js',vite:{plugins:[{
 name:'trace-progress',resolveId(id){if(id==='/__trace-progress-entry.js')return id},load(id){if(id==='/__trace-progress-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__trace-progress',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__trace-progress','<html><body><div id="root"></div><script type="module" src="/__trace-progress-entry.js"></script></body></html>'))}catch(e){next(e)}})}
}]}})

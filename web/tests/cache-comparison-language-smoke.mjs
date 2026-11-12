import assert from 'node:assert/strict'
import {spawnSync} from 'node:child_process'
import {fileURLToPath} from 'node:url'
import {browserScenario} from './browser-scenario.mjs'
import {fixturePython} from './python-fixture.mjs'

const source=`
import json
from harness.core.language import current_language
from harness.llm.prompt_cache import compare_prefix
old = {'model': 'old', 'messages': [], 'tools': [], 'temperature': 1}
new = {'model': 'new', 'messages': [], 'tools': [{'name': 'Original tool'}], 'temperature': 0, 'top_p': 1}
original = json.dumps([old, new])
rows = []
for language in ('en', 'zh-CN', 'en'):
 with current_language.set(language):
  rows.append(compare_prefix(old, new, same_endpoint=False))
assert rows[0] == rows[1] == rows[2]
assert json.dumps([old, new]) == original
assert rows[0]['changed_settings'] == ['temperature', 'top_p']
print(json.dumps(rows[0]))
`
const result=spawnSync(fixturePython,['-c',source],{cwd:fileURLToPath(new URL('../../',import.meta.url)),encoding:'utf8'})
assert.equal(result.status,0,result.stderr)
const comparison=JSON.parse(result.stdout)
assert.deepEqual(comparison.changes,['endpoint','model','tools','settings'])
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {TraceDetail} from '/src/components/inspector/RequestTracePanel.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
await setLocale('en');window.locale=setLocale;window.copy=t
createRoot(document.getElementById('root')).render(React.createElement(TraceDetail,{sessionId:'parent',traceId:'trace',onBack:()=>{},onSelect:()=>{}}))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[],requests=[]
 page.on('pageerror',error=>errors.push(error.message))
 await page.route('**/api/v1/**',route=>{
  const url=new URL(route.request().url());requests.push(url.pathname+url.search)
  assert.equal(route.request().method(),'GET')
  if(url.searchParams.has('part')&&url.searchParams.get('part')!=='summary')return route.fulfill({json:{text:'Original request 原文',offset:0,total_chars:Array.from('Original request 原文').length,next_offset:null}})
  assert.equal(url.pathname,'/api/v1/sessions/parent/requests/trace')
  return route.fulfill({json:{request:{id:'trace',session_id:'child',request_id:'fixture',model:'Original model',endpoint:'Original endpoint',status:'completed',body_bytes:0},messages:[],parameters:{},tool_names:[],composition:{normalized_bytes:0,groups:[],largest_inputs:[],largest_tools:[]},cache_comparison:{previous_id:'previous',...comparison}}})
 })
 await page.goto(server.resolvedUrls.local[0]+'__cache-comparison')
 for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900})
  await page.evaluate(async({locale,theme})=>{await window.locale(locale);document.documentElement.className=theme},{locale,theme})
  for(const change of comparison.changes){
   const expected=await page.evaluate(change=>window.copy('ui.requestStats.cacheChange.'+change,{settings:['temperature','top_p'].join(window.copy('ui.common.list_separator'))}),change)
   await page.getByText(expected,{exact:true}).waitFor()
   if(change==='settings'&&locale==='en')assert.equal(expected,'Request parameters changed: temperature, top_p')
  }
 }
 await page.getByText('Original request 原文',{exact:true}).waitFor()
 assert.equal(await page.getByRole('alert').count(),0)
 assert.deepEqual(errors,[])
 assert.deepEqual(requests.sort(),[
  '/api/v1/sessions/parent/requests/trace',
  '/api/v1/sessions/parent/requests/trace?part=summary',
  '/api/v1/sessions/parent/requests/trace?part=body_formatted&index=0&offset=0',
 ].sort())
 await page.close()
 console.log('Cache comparison: four Python change identities and complete templates across English/Chinese/English without refetching')
},{entry:'/__cache-comparison-entry.js',vite:{plugins:[{name:'cache-comparison-fixture',resolveId(id){if(id==='/__cache-comparison-entry.js')return id},load(id){if(id==='/__cache-comparison-entry.js')return entry},configureServer(server){server.middlewares.use('/__cache-comparison',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__cache-comparison','<html><body><div id="root"></div><script type="module" src="/__cache-comparison-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})

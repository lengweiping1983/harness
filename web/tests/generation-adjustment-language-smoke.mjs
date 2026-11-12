import assert from 'node:assert/strict'
import {spawnSync} from 'node:child_process'
import {fileURLToPath} from 'node:url'
import {browserScenario} from './browser-scenario.mjs'
import {fixturePython} from './python-fixture.mjs'

const source = `
import json
from harness.core.language import current_language
from harness.llm.request_limits import normalize_request
cases = [
 ({'max_tokens': 100, 'temperature': 9, 'reasoning': {'effort': 'invalid'}, 'extra': float('nan')}, {'max_output_tokens': 50}),
 ({'max_tokens': 100}, {'context_window': 60}),
 ({'reasoning': {'effort': 'high'}}, {'supports_thinking': False}),
 ({'reasoning': {'max_tokens': 10}}, {}),
 ({'max_tokens': 100, 'reasoning': {'max_tokens': 30}}, {'reasoning_min_tokens': 20, 'reasoning_max_tokens': 10}),
]
results = []
for language in ('en', 'zh-CN', 'en'):
 with current_language.set(language):
  results.append([normalize_request(body, caps) for body, caps in cases])
assert results[0] == results[1] == results[2]
print(json.dumps(results[0], allow_nan=False))
`
const fixture = spawnSync(fixturePython, ['-c', source], {cwd:fileURLToPath(new URL('../../',import.meta.url)),encoding:'utf8'})
assert.equal(fixture.status,0,fixture.stderr)
const cases=JSON.parse(fixture.stdout)
assert.equal(cases[0][0].max_tokens,50)
assert.equal('temperature' in cases[0][0],false)
assert.equal('extra' in cases[0][0],false)
const copies=cases.flatMap(([,parameters])=>parameters.adjustments)
assert.equal(new Set(copies.map(copy=>copy.key)).size,8)
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {GenerationDock} from '/src/components/chat/GenerationDock.tsx'
import {readGeneration} from '/src/lib/generation.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t
const adjustments=${JSON.stringify(copies)}
window.adjustments=adjustments;window.original=JSON.stringify(adjustments)
const generation=readGeneration({request_id:'fixture',phase:'waiting',adjustments})
createRoot(document.getElementById('root')).render(React.createElement(GenerationDock,{generation,connection:'closed',stopping:false}))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[],requests=[]
 page.on('pageerror',error=>errors.push(error.message))
 await page.route('**/api/v1/**',route=>{requests.push(route.request().url());return route.fulfill({status:500})})
 await page.goto(server.resolvedUrls.local[0]+'__generation-adjustments')
 await page.locator('summary').click()
 for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900})
  await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme},{locale,theme})
  for(const copy of copies){
   const label=await page.evaluate(copy=>window.copy(copy.key,copy.params),copy)
   await page.getByText(label,{exact:true}).waitFor()
  }
  assert.equal(await page.evaluate(()=>JSON.stringify(window.adjustments)===window.original),true)
 }
 assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);await page.close()
 console.log('Generation adjustments: eight Python resource identities, unchanged request normalization and English/Chinese/English rendering without requests')
},{entry:'/__generation-adjustments-entry.js',vite:{plugins:[{name:'generation-adjustments-fixture',resolveId(id){if(id==='/__generation-adjustments-entry.js')return id},load(id){if(id==='/__generation-adjustments-entry.js')return entry},configureServer(server){server.middlewares.use('/__generation-adjustments',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__generation-adjustments','<html><body><div id="root"></div><script type="module" src="/__generation-adjustments-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})

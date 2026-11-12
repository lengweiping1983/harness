import assert from 'node:assert/strict'
import {execFileSync} from 'node:child_process'
import {readFileSync} from 'node:fs'
import {fixturePython} from './python-fixture.mjs'
import {browserScenario} from './browser-scenario.mjs'
const schemas=JSON.parse(execFileSync(fixturePython,['-c',"import json;from harness.web.protocol import SCHEMAS;print(json.dumps({name:SCHEMAS[name]['properties'] for name in ('MemoryReference','MemoryLayerPreview')}))"],{cwd:new URL('../../',import.meta.url),encoding:'utf8'}))
const reference=schemas.MemoryReference,layer=schemas.MemoryLayerPreview
const resources=Object.fromEntries(['en','zh-CN'].map(locale=>[locale,JSON.parse(readFileSync(new URL('../src/i18n/locales/'+locale+'.json',import.meta.url),'utf8'))]))
const references=reference.source_status.enum.flatMap((source_status,index)=>reference.derivation_status.enum.flatMap(derivation_status=>reference.reason.enum.map(reason=>({id:'Original 原文 '+index+' '+derivation_status+' '+reason,revision:3,reason,source_status,derivation_status}))))
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {MemoryRecallPreview} from '/src/components/overlays/MemoryRecallPreview.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;useAppStore.setState({activeSessionId:'fixture'})
createRoot(document.getElementById('root')).render(React.createElement(MemoryRecallPreview,{sessionId:'fixture'}))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[];let reads=0
 page.on('pageerror',error=>errors.push(error.message))
 const report={session_id:'fixture',source_seq:37,budget:1000,used_chars:50,layers:layer.scope.enum.map((scope,index)=>({scope,status:layer.status.enum[index],budget:1000,recall:index===0?{budget:1000,used_chars:50,total:references.length,selected:references,skipped_budget:[],suppressed:[],skipped_query_limit:0,unmatched:0}:null,error_code:index===1?'Original diagnostic 原文':''}))}
 await page.route('**/api/v1/**',route=>{assert.equal(route.request().method(),'GET');assert.ok(route.request().url().endsWith('/memory/preview'));reads++;return route.fulfill({json:report})})
 await page.goto(server.resolvedUrls.local[0]+'__memory-resource-chain')
 await page.getByRole('button',{name:resources.en['ui.memory.preview.show'],exact:true}).click()
 await page.getByText(resources.en['ui.memory.preview.selected'],{exact:true}).click()
 for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  const region=page.getByRole('region',{name:resources[locale]['ui.memory.preview.title'],exact:true});await region.waitFor()
  for(const item of report.layers){
   const scope=resources[locale]['ui.memory.scope.'+item.scope],status=resources[locale]['ui.memory.preview.state.'+item.status];assert.ok(scope&&status)
   await region.getByRole('heading',{name:scope+' · '+status,exact:true}).waitFor()
  }
  const items=region.locator('li');assert.equal(await items.count(),references.length)
  const expected=await page.evaluate(references=>references.map(item=>window.copy('ui.memory.preview.reason.'+item.reason)+' · '+window.copy('ui.memory.preview.source',{state:window.copy('ui.memory.preview.state.'+item.source_status)})+' · '+window.copy('ui.memory.preview.derivation',{state:window.copy('ui.memory.preview.state.'+item.derivation_status)})),references)
  for(let index=0;index<references.length;index++){
   const item=references[index]
   for(const key of ['ui.memory.preview.reason.'+item.reason,'ui.memory.preview.state.'+item.source_status,'ui.memory.preview.state.'+item.derivation_status])assert.ok(resources[locale][key],locale+' '+key)
   assert.equal(await items.nth(index).locator('p').nth(1).textContent(),expected[index]);assert.ok((await items.nth(index).textContent()).includes(item.id))
  }
  await region.getByText('Original diagnostic 原文',{exact:true}).waitFor();assert.equal(reads,1)
 }
 assert.deepEqual(errors,[]);await page.close();console.log('Memory preview protocol enums: all reason/source/derivation combinations and scope/layer states switch en→zh→en without extra reads')
},{entry:'/__memory-resource-chain-entry.js',vite:{plugins:[{name:'memory-resource-chain',resolveId(id){if(id==='/__memory-resource-chain-entry.js')return id},load(id){if(id==='/__memory-resource-chain-entry.js')return entry},configureServer(server){server.middlewares.use('/__memory-resource-chain',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__memory-resource-chain','<html><body><div id="root"></div><script type="module" src="/__memory-resource-chain-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})

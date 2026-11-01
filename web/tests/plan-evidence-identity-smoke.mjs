import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {PlanEvidence} from '/src/components/inspector/PlanEvidence.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t
createRoot(document.getElementById('root')).render(React.createElement(PlanEvidence,{sessionId:'owner',planId:'plan',evidence:{call_id:'call',source_seq:7,name:'FixtureTool',summary:'Original summary 原文',truncated:false}}))
`
await browserScenario(async({server,browser})=>{
 for(const kind of ['call','source','offset','pagination','valid']){
  const page=await browser.newPage();const errors=[];let reads=0;page.on('pageerror',e=>errors.push(e.message))
  await page.route('**/api/v1/**',route=>{
   reads++;assert.equal(route.request().method(),'GET')
   const text=kind==='valid'?'Original 😀 原文':'Foreign evidence',end=Array.from(text).length
   const evidence={call_id:kind==='call'?'other':'call',source_seq:kind==='source'?8:7,name:'FixtureTool',offset:kind==='offset'?1:0,text,total_chars:end,next_offset:kind==='pagination'?3:null}
   return route.fulfill({json:{evidence}})
  })
  await page.goto(server.resolvedUrls.local[0]+'__plan-evidence-identity')
  await page.locator('summary').click()
  await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.components.inspector.PlanEvidence.b04c041e6e59')),exact:true}).click()
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
   if(kind==='valid')await page.getByText('Original 😀 原文',{exact:true}).waitFor()
   else {await page.getByRole('alert').filter({hasText:await page.evaluate(()=>window.copy('ui.http.invalidResponse'))}).waitFor();assert.equal(await page.getByText('Foreign evidence',{exact:true}).count(),0)}
   assert.equal(reads,1)
  }
  assert.deepEqual(errors,[]);await page.close();console.log(kind+': plan evidence identity and Unicode page bounds verified without rereading')
 }
},{entry:'/__plan-evidence-identity-entry.js',vite:{plugins:[{
 name:'plan-evidence-identity-fixture',resolveId(id){if(id==='/__plan-evidence-identity-entry.js')return id},load(id){if(id==='/__plan-evidence-identity-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__plan-evidence-identity',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__plan-evidence-identity','<html><body><div id="root"></div><script type="module" src="/__plan-evidence-identity-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})

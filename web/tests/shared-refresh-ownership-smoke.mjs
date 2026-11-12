import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {SharedTaskInput} from '/src/components/shared/SharedTaskInput.tsx'
import {collaboration} from '/src/lib/collaboration.ts'
import {useDraftStore} from '/src/store/drafts.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;window.reads=[];window.capabilities=[];window.hidden=false
Object.defineProperty(document,'hidden',{configurable:true,get:()=>window.hidden})
const scope=JSON.stringify(['shared','fixture','guest'])
useDraftStore.getState().setText(scope,'Original draft 原文')
window.draft=()=>useDraftStore.getState().bySession[scope].text
const read=(kind,id,signal)=>new Promise(resolve=>window.reads.push({kind,id,signal,resolve}))
collaboration.state=(id,signal)=>read('state',id,signal)
collaboration.queue=(id,signal)=>read('queue',id,signal)
window.respond=(batch,caps)=>{
 for(const row of window.reads.slice(batch*2,batch*2+2))row.resolve({ok:true,status:200,data:row.kind==='state'?{access:{members:{guest:caps}}}:{runs:[]}})
}
window.visibility=value=>{window.hidden=value;document.dispatchEvent(new Event('visibilitychange'))}
const root=createRoot(document.getElementById('root'))
window.mount=version=>root.render(React.createElement(SharedTaskInput,{sessionId:'fixture',subject:'guest',initialContribute:true,onCapabilities:caps=>window.capabilities.push({version,caps})}))
window.unmount=()=>root.unmount()
window.mount(0)
`
await browserScenario(async({server,browser})=>{
 for(const view of ['slow-poll','hidden','effect']){
  const page=await browser.newPage(),errors=[],requests=[]
  page.on('pageerror',error=>errors.push(error.message))
  await page.route('**/api/v1/**',route=>{requests.push(route.request().url());return route.fulfill({status:500,json:{error:'Unexpected request'}})})
  await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
  await page.goto(server.resolvedUrls.local[0]+'__shared-refresh')
  await page.waitForFunction(()=>window.reads?.length===2)
  const editor=page.locator('.composer-editor');await editor.waitFor();assert.equal(await editor.inputValue(),'Original draft 原文')
  if(view==='slow-poll'){
   await page.clock.runFor(20000)
   assert.equal(await page.evaluate(()=>window.reads.length),2,'Slow reads must finish without being replaced by each polling tick')
   await page.evaluate(()=>window.respond(0,['read']))
   await editor.waitFor({state:'hidden'})
   await page.clock.runFor(5000)
  }
  if(view==='effect')await page.evaluate(()=>window.mount(1))
  if(view==='hidden'){
   await page.evaluate(()=>window.visibility(true));await page.clock.runFor(20000)
   assert.equal(await page.evaluate(()=>window.reads.length),2,'Hidden input must not keep polling')
   await page.evaluate(()=>window.respond(0,['read','contribute']))
   await page.evaluate(()=>window.visibility(false))
  }
  await page.waitForFunction(()=>window.reads.length===4)
  await page.evaluate(()=>window.respond(1,['read']))
  await editor.waitFor({state:'hidden'})
  if(view==='effect')await page.evaluate(()=>window.respond(0,['read','contribute']))
  await page.clock.runFor(32)
  assert.equal(await editor.count(),0,'Older capability response must not restore revoked contribution')
  assert.deepEqual(await page.evaluate(()=>window.capabilities),Array.from({length:view==='slow-poll'?2:1},()=>({version:view==='effect'?1:0,caps:['read']})))
  if(view!=='slow-poll')assert.equal(await page.evaluate(()=>window.reads.slice(0,2).every(row=>row.signal?.aborted)),true,'Superseded reads must be aborted')
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
   assert.equal(await editor.count(),0);assert.equal(await page.evaluate(()=>window.draft()),'Original draft 原文')
   assert.equal(await page.evaluate(()=>window.reads.length),4)
  }
  await page.clock.runFor(5000);await page.waitForFunction(()=>window.reads.length===6)
  await page.evaluate(()=>window.unmount())
  assert.equal(await page.evaluate(()=>window.reads.slice(4).every(row=>row.signal?.aborted)),true)
  await page.evaluate(()=>window.respond(2,['read','contribute']))
  await page.clock.runFor(32)
  assert.equal(await page.evaluate(()=>window.capabilities.length),view==='slow-poll'?2:1,'Closed instance must not publish late capabilities')
  assert.deepEqual(requests,[]);assert.deepEqual(errors,[])
  await page.close();console.log('Shared refresh '+view+': latest capabilities only, draft preserved, hidden and closed reads aborted')
 }
},{entry:'/__shared-refresh-entry.js',vite:{plugins:[{
 name:'shared-refresh-fixture',resolveId(id){if(id==='/__shared-refresh-entry.js')return id},load(id){if(id==='/__shared-refresh-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__shared-refresh',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__shared-refresh','<html><body><div id="root"></div><script type="module" src="/__shared-refresh-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})

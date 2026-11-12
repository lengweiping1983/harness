import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {RuntimeTab} from '/src/components/inspector/RuntimeTab.tsx'
import {api} from '/src/lib/api.ts'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;window.reads=[]
api.runtime=(owner,signal)=>new Promise(resolve=>window.reads.push({owner,signal,resolve}))
api.subagentHistory=(owner,before)=>new Promise(resolve=>window.historyReads.push({owner,before,resolve}))
window.historyReads=[];window.payloads=[]
window.answer=(index,failed=false)=>window.reads[index].resolve(failed?{ok:false,message:'Controlled read failure',copy:{key:'ui.http.invalidResponse'}}:{ok:true,data:window.payloads[index]??{terminals:[],subagents:[],jobs:[]}})
useAppStore.setState({activeSessionId:'owner'})
const root=createRoot(document.getElementById('root'))
window.show=active=>root.render(React.createElement('main',{className:'min-h-screen bg-surface p-3 text-ink'},React.createElement(RuntimeTab,{active,showAgents:true})))
window.show(true)
`

await browserScenario(async({server,browser})=>{
 const page=await browser.newPage({viewport:{width:390,height:900}}),errors=[]
 page.setDefaultTimeout(5000)
 page.on('pageerror',error=>errors.push(error.message))
 await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
 await page.goto(server.resolvedUrls.local[0]+'__runtime-empty-refresh')
 await page.waitForFunction(()=>window.reads.length===1)
 await page.evaluate(()=>window.payloads[0]={terminals:[],subagents:[],subagents_has_more:true,subagents_next_before:'agent-cursor',jobs:[]})
 await page.evaluate(()=>window.answer(0))
 await page.getByRole('heading',{name:await page.evaluate(()=>window.copy('ui.components.inspector.RuntimeTab.1b26650b7a56'))}).waitFor()
 await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.showMore'))}).click()
 await page.waitForFunction(()=>window.historyReads.length===1)
 assert.equal(await page.evaluate(()=>window.historyReads[0].before),'agent-cursor','History paging remains available when the current list is empty')
 await page.evaluate(()=>window.historyReads[0].resolve({ok:true,data:{agents:[],has_more:false,next_before:null}}))
 await page.clock.runFor(2000)
 await page.waitForFunction(()=>window.reads.length===2)
 await page.evaluate(()=>window.answer(1,true))
 await page.getByRole('alert').waitFor()
 const matrix=async check=>{
  for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900})
   await page.evaluate(async({locale,dark})=>{await window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
   await page.getByRole('heading',{name:locale==='en'?'Background tasks':'后台任务',exact:true}).waitFor()
   await page.getByRole('button',{name:locale==='en'?'Refresh background tasks':'刷新后台任务状态',exact:true}).waitFor()
   await check(locale)
   assert.equal(await page.locator('body').evaluate(node=>node.scrollWidth<=innerWidth+1),true)
  }
 }
 await matrix(async()=>{
  await page.getByRole('alert').getByText(await page.evaluate(()=>window.copy('ui.http.invalidResponse')),{exact:false}).waitFor()
  assert.equal(await page.getByText(await page.evaluate(()=>window.copy('ui.components.inspector.RuntimeTab.dee44493c8d2')),{exact:true}).count(),0,'An unconfirmed empty state must not claim there are no jobs')
  await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.runtime.refresh')),exact:true}).waitFor()
 })
 assert.equal(await page.evaluate(()=>window.reads.length),2,'Language and theme changes must not restart reads')
 const refresh=page.getByRole('button',{name:'Refresh background tasks',exact:true})
 await refresh.evaluate(button=>{button.click();button.click()})
 await page.waitForFunction(()=>window.reads.length===3)
 assert.equal(await refresh.isDisabled(),true,'An in-flight refresh must not overlap another read')
 await page.clock.runFor(10000)
 assert.equal(await page.evaluate(()=>window.reads.length),3,'Polling and manual refresh must share one read')
 await page.evaluate(()=>window.answer(2))
 await page.getByText(await page.evaluate(()=>window.copy('ui.components.inspector.RuntimeTab.dee44493c8d2')),{exact:true}).waitFor()
 assert.equal(await page.getByRole('alert').count(),0,'A confirmed empty read clears the old error')
 await refresh.click()
 await page.waitForFunction(()=>window.reads.length===4)
 await page.evaluate(()=>window.show(false))
 await page.waitForFunction(()=>window.reads[3].signal.aborted)
 await page.evaluate(()=>window.answer(3,true))
 await page.clock.runFor(10000)
 assert.equal(await page.evaluate(()=>window.reads.length),4,'Hidden panels stop reading')
 assert.equal(await page.getByRole('alert').count(),0,'Hidden stale errors must not reappear')
 await page.evaluate(()=>window.show(true))
 await page.waitForFunction(()=>window.reads.length===5)
 await page.evaluate(()=>window.answer(4,true))
 await page.getByRole('alert').waitFor()
 assert.deepEqual(errors,[])
 await page.close()
 console.log('Runtime empty state: refresh failures stay visible; manual and periodic reads serialize; recovery, hidden cancellation, en→zh→en and theme/width matrix passed')
},{entry:'/__runtime-empty-refresh-entry.js',vite:{plugins:[{
 name:'runtime-empty-refresh-fixture',resolveId(id){if(id==='/__runtime-empty-refresh-entry.js')return id},load(id){if(id==='/__runtime-empty-refresh-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__runtime-empty-refresh',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__runtime-empty-refresh','<html><head><link rel="icon" href="data:,"></head><body><div id="root"></div><script type="module" src="/__runtime-empty-refresh-entry.js"></script></body></html>'))}catch(error){next(error)}})},
}]}})

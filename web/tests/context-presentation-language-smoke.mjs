import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ContextSettingsPanel} from '/src/components/overlays/ContextSettingsPanel.tsx'
import {ContextCompactControl} from '/src/components/inspector/ContextCompactControl.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t;setLocale('en')
createRoot(document.getElementById('root')).render(React.createElement(React.Fragment,null,React.createElement(ContextSettingsPanel),React.createElement(ContextCompactControl,{sessionId:'fixture',onComplete:()=>{}})))
`
await browserScenario(async({server,browser})=>{
 for(const mode of ['compact','project','failed']){
  const page=await browser.newPage(),errors=[],requests=[]
  page.on('pageerror',error=>errors.push(error.message))
  await page.route('**/api/v1/**',route=>{
   const request=route.request();requests.push(request.method())
   if(request.url().includes('/context/compact'))return route.fulfill({json:{id:'run',session_id:'fixture',kind:'context_compact',text:mode==='project'?'project':'',status:mode==='failed'?'failed':'completed',created_at:1,result:{changed:true,before_tokens:12345,after_tokens:1234,projected_results:2},error:mode==='failed'?{message:'Original failure 原文'}:null}})
   return route.fulfill({json:{config:{permission_policy:'request_approval',context_auto_manage:true,context_working_budget:10000,summary_max_tokens:null,output_reserve:1000,context_window:12000,tool_result_max_chars:null,tool_schema_mode:'auto',max_steps:null,token_budget:null,network_timeout:60},effective:{model:'Fixture model',context_window:12000,max_tokens:2000,sources:['manual']},tools:[],skills:[]}})
  })
  await page.goto(server.resolvedUrls.local[0]+'__context-presentation')
  await page.getByText(await page.evaluate(()=>window.copy('ui.context.advanced')),{exact:true}).click()
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
   const expected=await page.evaluate(({locale,mode})=>({window:window.copy('ui.context.window',{count:(12000).toLocaleString(locale)}),result:mode==='failed'?window.copy('ui.context.failed',{reason:'Original failure 原文'}):window.copy('ui.context.completedUsage',{result:window.copy(mode==='project'?'ui.context.projectCompleted':'ui.context.completed',{count:2}),before:(12345).toLocaleString(locale),after:(1234).toLocaleString(locale)})}),{locale,mode})
   for(const text of Object.values(expected))await page.getByText(text,{exact:true}).waitFor()
   for(const id of ['efficient','balanced','extended']){
    const title=await page.evaluate(id=>window.copy('ui.context.preset.'+id),id)
    const note=await page.evaluate(id=>window.copy('ui.context.preset.'+id+'.note'),id)
    await page.getByRole('button').filter({has:page.getByText(title,{exact:true})}).getByText(note,{exact:true}).waitFor()
    const budget=await page.evaluate(({id,locale})=>window.copy(id==='extended'?'ui.context.presetBudgetLimited':'ui.context.presetBudget',{percent:{efficient:40,balanced:60,extended:80}[id],count:({efficient:4800,balanced:7200,extended:9200}[id]).toLocaleString(locale)}),{id,locale})
    await page.getByRole('button').filter({has:page.getByText(title,{exact:true})}).getByText(budget,{exact:true}).waitFor()
    assert.equal(await page.getByRole('button').filter({has:page.getByText(title,{exact:true})}).getAttribute('aria-pressed'),id==='balanced'?'true':'false')
   }
   for(const key of ['tool_result_max_chars','summary_max_tokens','output_reserve','context_window']){
    const label=await page.evaluate(key=>window.copy('ui.context.'+key),key)
    await page.getByRole('spinbutton',{name:label,exact:true}).waitFor()
   }
   assert.equal(requests.length,2);assert.ok(requests.every(method=>method==='GET'))
  }
  assert.deepEqual(errors,[]);await page.close();console.log('Context presentation uses complete resources without repeating reads/writes: '+mode)
 }
},{entry:'/__context-presentation-entry.js',vite:{plugins:[{name:'context-presentation-fixture',resolveId(id){if(id==='/__context-presentation-entry.js')return id},load(id){if(id==='/__context-presentation-entry.js')return entry},configureServer(server){server.middlewares.use('/__context-presentation',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__context-presentation','<html><body><div id="root"></div><script type="module" src="/__context-presentation-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})

import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ContextSettingsPanel} from '/src/components/overlays/ContextSettingsPanel.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;window.calls=[]
let config={context_auto_manage:true,context_working_budget:10000,tool_result_max_chars:null,tool_schema_mode:'auto',summary_max_tokens:null,output_reserve:1000,context_window:12000}
window.fetch=async(path,options={})=>{
 const method=options.method||'GET';window.calls.push({method,body:options.body})
 if(method==='PATCH')await new Promise(resolve=>{window.release=()=>{config={...config,...JSON.parse(options.body)};resolve()}})
 return new Response(JSON.stringify({config,effective:{context_window:12000}}),{headers:{'Content-Type':'application/json'}})
}
createRoot(document.getElementById('root')).render(React.createElement(ContextSettingsPanel))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.goto(server.resolvedUrls.local[0]+'__context-save-admission')
 const copy=key=>page.evaluate(key=>window.copy(key),key)
 await page.getByRole('button',{name:await copy('ui.context.preset.efficient'),exact:false}).click()
 const save=page.getByRole('button',{name:await copy('ui.context.save'),exact:true})
 await save.evaluate(button=>{button.dispatchEvent(new MouseEvent('click',{bubbles:true}));button.dispatchEvent(new MouseEvent('click',{bubbles:true}))})
 await page.waitForFunction(()=>typeof window.release==='function')
 assert.equal(await page.evaluate(()=>window.calls.filter(call=>call.method==='PATCH').length),1,'Same-event saves must share one accepted write')
 for(const locale of ['zh-CN','en']){
  await page.evaluate(locale=>window.locale(locale),locale)
  assert.equal(await page.getByRole('button',{name:await copy('ui.context.saving'),exact:true}).isDisabled(),true)
  assert.equal(await page.evaluate(()=>window.calls.length),2)
 }
 await page.evaluate(()=>window.release())
 await page.getByText(await copy('ui.context.saved'),{exact:true}).waitFor()
 const calls=await page.evaluate(()=>window.calls)
 assert.deepEqual(calls.map(call=>call.method),['GET','PATCH','GET'])
 assert.deepEqual(JSON.parse(calls[1].body),{context_working_budget:4800,tool_result_max_chars:8000,summary_max_tokens:4096})
 for(const locale of ['en','zh-CN','en'])for(const [dark,width] of [[false,390],[true,1440]]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  await page.getByText(await copy('ui.context.saved'),{exact:true}).waitFor()
  assert.equal(await page.evaluate(()=>window.calls.length),3)
 }
 assert.deepEqual(errors,[]);await page.close();console.log('Context save: same-event admission is single, language changes preserve pending writes and localized receipt without repeated reads')
},{entry:'/__context-save-admission-entry.js',vite:{plugins:[{name:'context-save-admission-fixture',resolveId(id){if(id==='/__context-save-admission-entry.js')return id},load(id){if(id==='/__context-save-admission-entry.js')return entry},configureServer(server){server.middlewares.use('/__context-save-admission',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__context-save-admission','<html><body><div id="root"></div><script type="module" src="/__context-save-admission-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})

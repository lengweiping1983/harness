import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry = `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {PluginSourceEditor} from '/src/components/overlays/PluginSourceEditor.tsx'
import {usePluginSourceDrafts,sourceDirty} from '/src/store/pluginSourceDrafts.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t;window.changes=0
window.dirty=()=>Object.values(usePluginSourceDrafts.getState().drafts).some(sourceDirty)
setLocale('en')
createRoot(document.getElementById('root')).render(React.createElement(PluginSourceEditor,{root:'/fixture',onReview:()=>{},onChanged:()=>window.changes++}))
`
await browserScenario(async({server,browser})=>{
 for(const mode of ['malformed','root','file','content','executable','valid'])for(const foreign of (mode==='valid'?[false]:[false,true])){
  const page=await browser.newPage();page.setDefaultTimeout(15000)
  await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
  let writes=0,reads=0;const errors=[];page.on('pageerror',error=>errors.push(error.message))
  await page.route('**/api/v1/**',route=>{
   const request=route.request(),url=new URL(request.url())
   if(request.method()==='PATCH'){writes++;return mode==='malformed' ? route.fulfill({status:200,body:'invalid json',contentType:'application/json'}) : route.fulfill({json:{root:mode==='root'?'/other':'/fixture',file:mode==='file'?'other.md':'SKILL.md',content:mode==='content'?'Other content':'Changed 原文',revision:'b'.repeat(64),executable:mode==='executable',read_only:false}})}
   if(url.pathname.endsWith('/source/file')){reads++;return route.fulfill({json:{root:writes&&foreign?'/other':'/fixture',file:'SKILL.md',content:writes?'Changed 原文':'Original',revision:(writes?'b':'a').repeat(64),executable:false,read_only:false}})}
   return route.fulfill({json:{root:'/fixture',files:['SKILL.md'],read_only:false,executable_supported:false}})
  })
  await page.goto(server.resolvedUrls.local[0]+'__source-save-receipt')
  await page.locator('textarea').fill('Changed 原文')
  const before=await page.evaluate(()=>window.changes)
  await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.extensions.saveSource')),exact:true}).click()
  if(foreign)await page.getByRole('alert').waitFor()
  else await page.waitForFunction(()=>!window.dirty())
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900})
   await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
   const expected=await page.evaluate(foreign=>foreign?window.copy('ui.extensions.unknownResult',{reason:window.copy('ui.http.invalidResponse')}):window.copy('ui.extensions.sourceSaved'),foreign)
   await page.waitForFunction(expected=>document.body.textContent.includes(expected),expected)
   assert.equal(await page.locator('textarea').inputValue(),'Changed 原文')
  }
  assert.equal(await page.evaluate(()=>window.dirty()),foreign)
  assert.equal(await page.evaluate(()=>window.changes),before+(foreign?0:1))
  assert.equal(writes,1);assert.equal(reads,mode==='valid'?1:2);assert.deepEqual(errors,[])
  await page.close();console.log(mode+': '+(foreign?'Foreign root cannot confirm save; localized uncertainty retains draft':'Matching save or readback confirms original write without replay'))
 }
},{entry:'/__source-save-receipt-entry.js',vite:{plugins:[{
 name:'source-save-receipt-fixture',resolveId(id){if(id==='/__source-save-receipt-entry.js')return id},load(id){if(id==='/__source-save-receipt-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__source-save-receipt',async(_req,res,next)=>{
  try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__source-save-receipt','<html><body><div id="root"></div><script type="module" src="/__source-save-receipt-entry.js"></script></body></html>'))}catch(error){next(error)}
 })}
}]}})

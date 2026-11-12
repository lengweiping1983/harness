import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {TerminalSetupPanel} from '/src/components/inspector/TerminalSetupPanel.tsx'
import {Toaster} from '/src/components/overlays/CommandPalette.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t;window.ready=0;setLocale('en')
createRoot(document.getElementById('root')).render(React.createElement(React.Fragment,null,React.createElement(TerminalSetupPanel,{active:true,enabled:true,onReady:()=>{window.ready++}}),React.createElement(Toaster)))
`
await browserScenario(async({server,browser})=>{
 for(const mode of ['408','wrong-post','invalid-json','wrong-poll','wrong-cancel','normal']){
  const page=await browser.newPage(),errors=[],requests=[];let id,complete=false
  page.on('pageerror',error=>errors.push(error.message))
  await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
  const job=(identity=id)=>({id:identity,status:complete?'succeeded':'running',phase:complete?'complete':'dependency',message:{key:'ui.terminalSetup.runtime.downloading'},error:null,started_at:1,ended_at:complete?2:null})
  await page.route('**/api/v1/**',route=>{
   const request=route.request(),url=new URL(request.url());requests.push({method:request.method(),path:url.pathname,query:url.search})
   if(url.pathname.endsWith('/setup'))return route.fulfill({json:{supported:true,ready:false,error:null,installation:null}})
   if(request.method()==='POST'){
    id=request.postDataJSON().request_id
    if(mode==='408')return route.fulfill({status:408,json:{code:'fixture/timeout',message:'Original timeout 原文'}})
    if(mode==='invalid-json')return route.fulfill({body:'not json',contentType:'application/json'})
    return route.fulfill({json:{installation:job(mode==='wrong-post'?'foreign':id)}})
   }
   if(request.method()==='DELETE'){assert.equal(request.postDataJSON().id,id);return route.fulfill({json:{installation:job('foreign')}})}
   assert.equal(request.method(),'GET');assert.equal(url.searchParams.get('id'),id)
   return route.fulfill({json:{installation:job(mode==='wrong-poll'&&!complete?'foreign':id)}})
  })
  await page.goto(server.resolvedUrls.local[0]+'__terminal-receipt')
  const label=key=>page.evaluate(key=>window.copy(key),key)
  await page.getByRole('button',{name:await label('ui.terminalSetup.install'),exact:true}).click()
  await page.getByRole('button',{name:await label('ui.terminalSetup.cancel'),exact:true}).waitFor()
  if(mode==='wrong-cancel'){
   const response=page.waitForResponse(response=>response.request().method()==='DELETE')
   await page.getByRole('button',{name:await label('ui.terminalSetup.cancel'),exact:true}).click();await response
   await page.getByRole('alert').filter({hasText:await label('ui.http.invalidResponse')}).waitFor()
  }
  if(mode!=='normal')await page.waitForFunction(()=>document.querySelector('[data-terminal-setup] [role="alert"]')||document.querySelector('[data-terminal-setup] [role="status"]')?.textContent.includes(window.copy('ui.terminalSetup.runtime.downloading')))
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
   await page.getByRole('button',{name:await label('ui.terminalSetup.cancel'),exact:true}).waitFor()
   assert.equal(await page.getByRole('button',{name:await label('ui.terminalSetup.install'),exact:true}).count(),0)
   if(mode==='normal')await page.getByRole('status').filter({hasText:await label('ui.terminalSetup.runtime.downloading')}).waitFor()
   assert.equal(await page.evaluate(()=>window.ready),0);assert.equal(requests.filter(x=>x.method==='POST').length,1)
  }
  complete=true;await page.clock.runFor(800)
  if(mode==='wrong-cancel')await page.locator('[data-terminal-setup]').waitFor({state:'detached'})
  else await page.waitForFunction(()=>window.ready===1)
  for(const locale of ['en','zh-CN','en']){
   await page.evaluate(locale=>window.locale(locale),locale)
   if(mode!=='wrong-cancel')await page.getByText(await label('ui.terminalSetup.ready'),{exact:true}).waitFor()
   assert.equal(await page.evaluate(()=>window.ready),mode==='wrong-cancel'?0:1);assert.equal(requests.filter(x=>x.method==='POST').length,1)
  }
  assert.deepEqual(errors,[]);await page.close();console.log('Terminal installation keeps original receipt, one write, and live-language success toast: '+mode)
 }
},{entry:'/__terminal-receipt-entry.js',vite:{plugins:[{name:'terminal-receipt-fixture',resolveId(id){if(id==='/__terminal-receipt-entry.js')return id},load(id){if(id==='/__terminal-receipt-entry.js')return entry},configureServer(server){server.middlewares.use('/__terminal-receipt',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__terminal-receipt','<html><body><div id="root"></div><script type="module" src="/__terminal-receipt-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})

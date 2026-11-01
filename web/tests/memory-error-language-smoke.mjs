import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import '/@vite/client'
import React from 'react'
import {createRoot} from 'react-dom/client'
import {MemoryRecallPreview} from '/src/components/overlays/MemoryRecallPreview.tsx'
import {useAppStore} from '/src/store/app.ts'
useAppStore.setState({activeSessionId:'fixture'})
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t;setLocale('en')
createRoot(document.getElementById('root')).render(React.createElement(MemoryRecallPreview,{sessionId:'fixture'}))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage();let reads=0;const errors=[];page.on('pageerror',error=>{errors.push(error.message);console.error(error.message)})
 await page.route('**/api/v1/sessions/fixture/memory/preview',route=>{reads++;return route.fulfill({status:200,body:'not json',contentType:'application/json'})})
 await page.goto(server.resolvedUrls.local[0]+'__memory-error');await page.getByRole('button').click();await page.getByRole('alert').waitFor()
 for(const locale of ['en','zh-CN','en']) for(const theme of ['light','dark']) for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
  const expected=await page.evaluate(()=>window.copy('ui.http.invalidResponse'))
  await page.getByText(expected,{exact:true}).waitFor();assert.equal(reads,1)
 }
 assert.deepEqual(errors,[]);await page.close();console.log('Memory preview: same saved error updates with direct locale subscription and no refetch')

},{entry:'/__memory-error-entry.js',vite:{plugins:[{name:'memory-error-language-fixture',resolveId(id){if(id==='/__memory-error-entry.js')return id},load(id){if(id==='/__memory-error-entry.js')return entry},configureServer(server){server.middlewares.use('/__memory-error',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__memory-error','<html><body><div id="root"></div><script type="module" src="/__memory-error-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})

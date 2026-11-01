import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import '/@vite/client'
import React from 'react'
import {createRoot} from 'react-dom/client'
import {FileOpenActions} from '/src/components/inspector/FileOpenActions.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t;setLocale('en')
createRoot(document.getElementById('root')).render(React.createElement(FileOpenActions,{path:'Original.txt',root:'/fixture'}))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage();let reads=0;const errors=[];page.on('pageerror',error=>{errors.push(error.message);console.error(error.message)})
 await page.route('**/api/v1/file/open',route=>{reads++;return route.fulfill({status:200,body:'not json',contentType:'application/json'})})
 await page.goto(server.resolvedUrls.local[0]+'__file-error');await page.locator('.file-open-main').click();await page.getByRole('alert').waitFor()
 for(const locale of ['en','zh-CN','en']) for(const theme of ['light','dark']) for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
  const expected=await page.evaluate(()=>window.copy('ui.http.invalidResponse'))
  await page.getByText(expected,{exact:true}).waitFor();assert.equal(reads,1)
 }
 assert.deepEqual(errors,[]);await page.close();console.log('File actions: saved protocol error updates across language changes without repeating desktop action')

},{entry:'/__file-error-entry.js',vite:{plugins:[{name:'file-error-language-fixture',resolveId(id){if(id==='/__file-error-entry.js')return id},load(id){if(id==='/__file-error-entry.js')return entry},configureServer(server){server.middlewares.use('/__file-error',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__file-error','<html><body><div id="root"></div><script type="module" src="/__file-error-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})

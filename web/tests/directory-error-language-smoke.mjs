import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import '/@vite/client'
import React from 'react'
import {createRoot} from 'react-dom/client'
import {DirectoryField} from '/src/components/ui/DirectoryField.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t;setLocale('en')
createRoot(document.getElementById('root')).render(React.createElement(DirectoryField,{value:'Original/path',label:'Original label',onChange:()=>{throw Error('Unexpected selection')}}))
`
await browserScenario(async({server,browser})=>{
 for(const mode of ['protocol','product','generic','external']) {
 const page=await browser.newPage();page.setDefaultTimeout(15000)
 let reads=0;const errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.route('**/api/v1/directories?*',route=>{
  reads++
  if(mode==='protocol')return route.fulfill({status:200,body:'not json',contentType:'application/json'})
  return route.fulfill({status:403,json:mode==='product'?{code:'access/denied',error:'宿主固定语言'}:mode==='generic'?{code:'external/failure',message:{invalid:true}}:{code:'external/failure',error:'Original failure 原文'}})
 })
 await page.goto(server.resolvedUrls.local[0]+'__directory-error');await page.getByRole('button').click();await page.getByRole('alert').waitFor()
 for(const locale of ['en','zh-CN','en']) for(const theme of ['light','dark']) for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
  const expected=await page.evaluate(mode=>mode==='external'?'Original failure 原文':window.copy(mode==='product'?'ui.collaboration.error.access/denied':mode==='generic'?'ui.http.failure':'ui.http.invalidResponse',mode==='product'?{code:'access/denied'}:mode==='generic'?{status:403}:undefined),mode)
  await page.getByText(expected,{exact:true}).waitFor();assert.equal(reads,1)
  assert.equal(await page.getByRole('textbox',{name:'Original label'}).inputValue(),'Original/path')
 }
 assert.deepEqual(errors,[]);await page.close();console.log('Directory field: '+mode+' error keeps identity across language changes without refetching or changing path')
 }

},{entry:'/__directory-error-entry.js',vite:{plugins:[{name:'directory-error-language-fixture',resolveId(id){if(id==='/__directory-error-entry.js')return id},load(id){if(id==='/__directory-error-entry.js')return entry},configureServer(server){server.middlewares.use('/__directory-error',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__directory-error','<html><body><div id="root"></div><script type="module" src="/__directory-error-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})

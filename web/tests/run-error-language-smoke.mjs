import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import '/@vite/client'
import React from 'react'
import {createRoot} from 'react-dom/client'
import {RunHistoryCard} from '/src/components/inspector/RunHistoryCard.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t;setLocale('en')
const props={sessionId:'fixture',busy:false,onContinue:()=>{throw Error('Unexpected continuation')},onOriginal:()=>{}}
createRoot(document.getElementById('root')).render(React.createElement(React.Fragment,null,
 React.createElement(RunHistoryCard,{...props,record:{session_id:'fixture',id:'run-fixture',status:'completed',summary:true,created_at:1700000000,text:'Original task',started_at:null,finished_at:null},expanded:true}),
 React.createElement(RunHistoryCard,{...props,record:{session_id:'fixture',id:'run-unknown',status:'failed',created_at:1700000001,text:'Unknown task',started_at:null,finished_at:null,error:{message:'Internal parser detail'}},expanded:true})
))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage();let reads=0;const errors=[];page.on('pageerror',error=>{errors.push(error.message);console.error(error.message)})
 await page.route('**/api/v1/sessions/fixture/runs/run-fixture',route=>{reads++;return route.fulfill({status:200,body:'not json',contentType:'application/json'})})
 await page.goto(server.resolvedUrls.local[0]+'__run-error');await page.getByRole('alert').waitFor()
 const generic=await page.evaluate(()=>window.copy('ui.runHistory.failureGeneric'))
 await page.getByText(generic,{exact:true}).waitFor()
 assert.equal(await page.getByText('Internal parser detail',{exact:true}).isVisible(),false,'Raw unknown errors stay out of the main status copy')
 await page.getByText(await page.evaluate(()=>window.copy('ui.components.inspector.RunHistoryCard.c26fb419db3c')),{exact:true}).last().click()
 await page.getByText('Internal parser detail',{exact:true}).waitFor()
 for(const locale of ['en','zh-CN','en']) for(const theme of ['light','dark']) for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
  const expected=await page.evaluate(()=>window.copy('ui.http.invalidResponse'))
  await page.getByText(expected,{exact:true}).waitFor();assert.equal(reads,1)
 }
 assert.deepEqual(errors,[]);await page.close();console.log('Run history: saved protocol error changes language without refetching or continuation')

},{entry:'/__run-error-entry.js',vite:{plugins:[{name:'run-error-language-fixture',resolveId(id){if(id==='/__run-error-entry.js')return id},load(id){if(id==='/__run-error-entry.js')return entry},configureServer(server){server.middlewares.use('/__run-error',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__run-error','<html><body><div id="root"></div><script type="module" src="/__run-error-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})

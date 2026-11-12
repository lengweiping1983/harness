import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import '/@vite/client'
import React from 'react'
import {createRoot} from 'react-dom/client'
import {TaskQueue} from '/src/components/inspector/TaskQueue.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t;setLocale('en')
window.reads=[];window.hidden=false;Object.defineProperty(document,'hidden',{get:()=>window.hidden,configurable:true});window.visibility=value=>{window.hidden=value;document.dispatchEvent(new Event('visibilitychange'))};window.fetch=(path,options={})=>new Promise(resolve=>window.reads.push({path:String(path),signal:options.signal,resolve}));createRoot(document.getElementById('root')).render(React.createElement(TaskQueue,{sessionId:'fixture'}))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.clock.install();await page.goto(server.resolvedUrls.local[0]+'__trace-error')
 await page.waitForFunction(()=>window.reads.length===2)
 await page.evaluate(()=>window.visibility(true))
 assert.equal(await page.evaluate(()=>window.reads.every(row=>row.signal?.aborted)),true,'Hidden queue must abort both pending reads')
 const count=await page.evaluate(()=>window.reads.length)
 await page.clock.runFor(10000);assert.equal(await page.evaluate(()=>window.reads.length),count)
 await page.evaluate(()=>window.reads.forEach(row=>row.resolve(new Response(JSON.stringify({error:'STALE QUEUE'}),{status:503}))))
 await page.clock.runFor(32);assert.equal(await page.getByText('STALE QUEUE',{exact:false}).count(),0)
 await page.evaluate(()=>window.visibility(false));await page.waitForFunction(()=>window.reads.length===4)
 for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.classList.toggle('dark',theme==='dark')},{locale,theme})
  assert.equal(await page.getByText('STALE QUEUE',{exact:false}).count(),0)
 }
 assert.deepEqual(errors,[]);await page.close();console.log('Queue hidden reads abort, stale failure stays isolated, visibility resumes both reads')

},{entry:'/__trace-error-entry.js',vite:{plugins:[{name:'trace-error-language-fixture',resolveId(id){if(id==='/__trace-error-entry.js')return id},load(id){if(id==='/__trace-error-entry.js')return entry},configureServer(server){server.middlewares.use('/__trace-error',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__trace-error','<html><body><div id="root"></div><script type="module" src="/__trace-error-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})

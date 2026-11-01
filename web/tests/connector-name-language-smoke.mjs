import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
import {withDeadline} from './fixture-cleanup.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ConnectorsTab} from '/src/components/overlays/ConnectorsTab.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
await setLocale('en');window.locale=setLocale;window.copy=t;window.writes=[]
const connectors=['constructor','toString','__proto__','ordinary'].map(name=>({name,transport:'http',url:'https://example.test/mcp',enabled:true,bound:false,connected:false,tool_count:0,oauth:{supported:true,state:'pending',mode:'code'}}))
const overview={skills:[],agents:[],connectors,enabled:{connectors:true,skills:true,agents:true}}
window.mount=()=>createRoot(document.getElementById('root')).render(React.createElement(ConnectorsTab,{overview,envMcp:[],busy:'',perform:async(...args)=>{window.writes.push(args[0]);return false}}))
window.mount()
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage();page.setDefaultTimeout(5000)
 const errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.addInitScript(()=>{
  window.completedOAuthReads=0
  const original=window.fetch
  window.fetch=async(...args)=>{
   const response=await original(...args)
   if(String(args[0]).endsWith('/oauth/status')){
    await response.clone().text()
    window.completedOAuthReads++
   }
   return response
  }
 })
 const calls=[]
 let entered
 const allRequests=new Promise(resolve=>{entered=resolve})
 let release
 const gate=new Promise(resolve=>{release=resolve})
 await page.route('**/api/v1/plugins/connectors/presets',route=>route.fulfill({json:{presets:[]}}))
 await page.route('**/api/v1/plugins/connectors/*/oauth/status',async route=>{
  const name=decodeURIComponent(new URL(route.request().url()).pathname.split('/').at(-3))
  calls.push(name)
  if(calls.length===4)entered()
  if(name==='__proto__'||name==='ordinary')await gate
  return route.fulfill({json:{supported:true,state:'authorized'}})
 })
 await page.clock.install()
 try {
 await page.goto(server.resolvedUrls.local[0]+'__connector-name-language')
 // 两个先完成的连接不能取消并重读同批尚未返回的其他连接。
 await page.waitForFunction(()=>document.querySelectorAll('[role="searchbox"],input[type="search"]').length===1)
 await withDeadline(()=>allRequests,10000,'OAuth batch requests')
 await page.waitForFunction(()=>window.completedOAuthReads===2)
 await page.clock.runFor(5000)
 assert.deepEqual(calls.toSorted(),['__proto__','constructor','ordinary','toString'])
 assert.equal(await page.getByRole('button',{name:'Sign out of OAuth',exact:true}).count(),0)
 release()
 await page.getByRole('button',{name:'Sign out of OAuth',exact:true}).nth(3).waitFor()
 assert.deepEqual(calls.sort(),['__proto__','constructor','ordinary','toString'])
 for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(async({locale,dark})=>{await window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  for(const name of ['constructor','toString','__proto__','ordinary']){
   const label=await page.evaluate(name=>window.copy('ui.catalog.disableNamed',{name}),name)
   await page.getByRole('switch',{name:label,exact:true}).waitFor()
  }
  assert.equal(await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.components.overlays.EscPanel.4f3708bc0266')),exact:true}).count(),4)
  assert.equal(calls.length,4,'Locale changes do not restart settled OAuth reads')
  assert.deepEqual(await page.evaluate(()=>window.writes),[])
 }
 assert.deepEqual(errors,[]);await page.close()
 } finally {release()}
 console.log('Connector names: inherited names preserve OAuth status and poll once; complete labels localize without writes across language/theme/width changes')
},{entry:'/__connector-name-language-entry.js',vite:{plugins:[{name:'connector-name-language-fixture',resolveId(id){if(id==='/__connector-name-language-entry.js')return id},load(id){if(id==='/__connector-name-language-entry.js')return entry},configureServer(server){server.middlewares.use('/__connector-name-language',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__connector-name-language','<html><body><div id="root"></div><script type="module" src="/__connector-name-language-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})

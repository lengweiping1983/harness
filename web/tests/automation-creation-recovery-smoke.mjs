import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {AutomationsPage} from '/src/components/overlays/AutomationsPage.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;window.posts=0;window.reads=0
useAppStore.setState({activeSessionId:'fixture',sessions:[{id:'fixture',title:'Original conversation 原文',cwd:'/fixture'}]})
const response=value=>new Response(JSON.stringify(value),{headers:{'Content-Type':'application/json'}})
window.fetch=async(path,options={})=>{
 if(path.endsWith('/preview'))return response({occurrences:[1800000000]})
 if(options.method==='POST'){
  window.posts++;const body=JSON.parse(options.body)
  localStorage.setItem('fixture:saved',JSON.stringify({...body,id:'auto_'+body.creation_id,enabled:Number(body.enabled),due:1800000000,running:0,failures:0,last_error:null}))
  return new Response('broken',{headers:{'Content-Type':'application/json'}})
 }
 window.reads++
 const saved=localStorage.getItem('fixture:saved')
 return response({automations:saved&&localStorage.getItem('fixture:recover')?[JSON.parse(saved)]:[],runs:[],scheduler:{running:true,owner:true,last_error:null,max_concurrent:1}})
}
createRoot(document.getElementById('root')).render(React.createElement(AutomationsPage))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
 const url=server.resolvedUrls.local[0]+'__automation-recovery'
 const label=suffix=>page.evaluate(suffix=>window.copy('ui.components.overlays.AutomationsPage.'+suffix),suffix)
 const openDraft=async name=>{
  await page.getByRole('button',{name:await label('6bee2372805a'),exact:true}).click()
  await page.clock.runFor(300)
  await page.getByRole('textbox',{name:await label('2479560deb33'),exact:true}).fill(name)
  await page.locator('textarea').fill('Original prompt 原文')
  await page.waitForFunction(()=>!document.querySelector('button[type=submit]').disabled)
 }
 await page.goto(url);await page.waitForFunction(()=>window.reads===1)
 await openDraft('Original automation 原文');await page.locator('button[type=submit]').click()
 await page.getByRole('alert').waitFor()
 assert.equal(await page.evaluate(()=>window.posts),1)
 const pending=await page.evaluate(()=>localStorage.getItem('harness:automation-creation:fixture'))
 assert.ok(pending)
 await page.locator('button[type=submit]').click();await page.getByRole('alert').waitFor()
 assert.equal(await page.evaluate(()=>window.posts),1,'pending creation is never replayed')
 await page.evaluate(()=>localStorage.setItem('fixture:recover','yes'))
 await page.reload();await page.getByText('Original automation 原文',{exact:true}).waitFor()
 await openDraft('New draft 原文');await page.locator('button[type=submit]').click()
 await page.getByRole('alert').waitFor()
 assert.equal(await page.evaluate(()=>window.posts),0,'reopening must only read the saved creation')
 assert.equal(await page.evaluate(()=>localStorage.getItem('harness:automation-creation:fixture')),null)
 const reads=await page.evaluate(()=>window.reads)
 for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900})
  await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
  const expected=await page.evaluate(()=>window.copy('ui.automations.creationRecovered'))
  await page.waitForFunction(expected=>document.querySelector('[role=alert]')?.textContent.includes(expected),expected)
  assert.equal(await page.locator('textarea').inputValue(),'Original prompt 原文')
  assert.equal(await page.getByRole('textbox',{name:await label('2479560deb33'),exact:true}).inputValue(),'New draft 原文')
  assert.equal(await page.evaluate(()=>window.posts),0);assert.equal(await page.evaluate(()=>window.reads),reads)
 }
 assert.deepEqual(errors,[]);await page.close()
},{entry:'/__automation-recovery-entry.js',vite:{plugins:[{name:'automation-recovery-fixture',resolveId(id){if(id==='/__automation-recovery-entry.js')return id},load(id){if(id==='/__automation-recovery-entry.js')return entry},configureServer(server){server.middlewares.use('/__automation-recovery',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__automation-recovery','<html><body><div id="root"></div><script type="module" src="/__automation-recovery-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})

import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {AutomationsPage} from '/src/components/overlays/AutomationsPage.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale(new URLSearchParams(location.search).get('locale'));window.copy=t
window.selected=[];window.settingsClosures=[];window.reads=0
useAppStore.setState({activeSessionId:'original',sessions:[],
 refreshSessions:()=>new Promise(resolve=>{window.finishRefresh=()=>resolve(true)}),
 selectSession:id=>window.selected.push(id),setSettingsOpen:open=>window.settingsClosures.push(open)})
window.fetch=async()=>{window.reads++;return new Response(JSON.stringify({automations:[],runs:[{
 id:'run',automation_id:'task',name:'Original run 原文',session_id:'target',started:1,finished:2,
 status:'completed',trigger:'manual',unread:0,result:'{"text":"Original output 原文"}'}],
 scheduler:{running:true,owner:true,last_error:null,max_concurrent:1}}),{headers:{'Content-Type':'application/json'}})}
const root=createRoot(document.getElementById('root'));window.unmount=()=>root.unmount()
root.render(React.createElement(AutomationsPage))
`
await browserScenario(async({server,browser})=>{
 for(const locale of ['en','zh-CN'])for(const detached of [false,true]){
  const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message))
  await page.goto(server.resolvedUrls.local[0]+'__automation-navigation?locale='+locale)
  await page.waitForFunction(()=>window.reads===1).catch(error=>{throw new Error(errors.join('\n'),{cause:error})})
  await page.getByRole('tab').nth(1).click()
  await page.getByRole('button',{name:/Original run/}).click()
  const label=await page.evaluate(()=>window.copy('ui.components.overlays.AutomationsPage.885f8c01983b'))
  await page.getByRole('button',{name:label,exact:true}).click()
  await page.waitForFunction(()=>typeof window.finishRefresh==='function')
  if(detached)await page.evaluate(()=>window.unmount())
  await page.evaluate(async()=>{window.finishRefresh();await new Promise(resolve=>setTimeout(resolve,0))})
  assert.deepEqual(await page.evaluate(()=>window.selected),detached?[]:['target'])
  assert.deepEqual(await page.evaluate(()=>window.settingsClosures),detached?[]:[false])
  assert.deepEqual(errors,[]);await page.close()
 }
},{entry:'/__automation-navigation-entry.js',vite:{plugins:[{name:'automation-navigation-fixture',resolveId(id){if(id==='/__automation-navigation-entry.js')return id},load(id){if(id==='/__automation-navigation-entry.js')return entry},configureServer(server){server.middlewares.use('/__automation-navigation',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__automation-navigation','<html><body><div id="root"></div><script type="module" src="/__automation-navigation-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})

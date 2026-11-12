import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {QueuedFollowups} from '/src/components/chat/QueuedFollowups.tsx'
import {useAppStore} from '/src/store/app.ts'
import {useChatStore,emptySession} from '/src/store/chat.ts'
import {setLocale,t} from '/src/i18n/index.ts'
window.copy=t;window.locale=setLocale;window.restored=[];window.switchTo=id=>useAppStore.setState({activeSessionId:id})
useAppStore.setState({activeSessionId:'A'})
useChatStore.setState({sessions:{A:{...emptySession(),connection:'open',queued:1}}})
createRoot(document.getElementById('root')).render(React.createElement(QueuedFollowups,{sessionId:'A',disabled:false,preserveDraft:false,onWorking:()=>{},onRestored:(entry,focus)=>window.restored.push({text:entry.text,focus})}))
`
await browserScenario(async({server,browser})=>{
 for(const language of ['en','zh-CN'])for(const mode of ['normal','switch','roundtrip']){
  const page=await browser.newPage();let release,enter;const gate=new Promise(r=>release=r),held=new Promise(r=>enter=r);let writes=0
  await page.route('**/api/v1/**',async route=>{
   if(route.request().method()==='DELETE'){writes++;enter();await gate;return route.fulfill({json:{removed:'entry'}})}
   return route.fulfill({json:{runs:[{id:'entry',text:'Original 原文',edit_version:'a'.repeat(64)}]}})
  })
  await page.goto(server.resolvedUrls.local[0]+'__queue-restore-focus');await page.evaluate(language=>window.locale(language),language)
  await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.components.chat.QueuedFollowups.9fd71ecb87c7')),exact:true}).click();await held
  await page.evaluate(mode=>{if(mode!=='normal')window.switchTo('B');if(mode==='roundtrip')window.switchTo('A')},mode)
  release();await page.waitForFunction(()=>window.restored.length===1)
  assert.deepEqual(await page.evaluate(()=>window.restored),[{text:'Original 原文',focus:mode==='normal'}]);assert.equal(writes,1)
  await page.close()
 }
 console.log('Queue withdrawal restores original draft once; switch and A-B-A suppress focus in both languages')
},{entry:'/__queue-restore-focus-entry.js',vite:{plugins:[{name:'queue-restore-focus-fixture',resolveId(id){if(id==='/__queue-restore-focus-entry.js')return id},load(id){if(id==='/__queue-restore-focus-entry.js')return entry},configureServer(server){server.middlewares.use('/__queue-restore-focus',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__queue-restore-focus','<html><body><div id="root"></div><script type="module" src="/__queue-restore-focus-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})

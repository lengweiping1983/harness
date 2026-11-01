import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {Conversation} from '/src/components/chat/Conversation.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
await setLocale('zh-CN')
useAppStore.setState({activeSessionId:'fixture'})
const blocks=Array.from({length:30},(_,index)=>({id:'message-'+index,kind:'user',at:index+1,text:'第 '+index+' 条历史消息：'+('保留原文 '.repeat(12))}))
createRoot(document.getElementById('root')).render(React.createElement('div',{style:{height:650,display:'flex'}},React.createElement(Conversation,{blocks,running:false,streaming:false,connection:'open',readOnly:true})))
`

await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[]
 page.on('pageerror',error=>errors.push(error.message))
 await page.goto(server.resolvedUrls.local[0]+'__conversation-reopen-jump')
 const scroll=page.locator('.conversation-scroll');await scroll.waitFor()
 const samples=[]
 for(const ms of [0,120,300,700,1300]){
  if(ms)await page.waitForTimeout(ms)
  samples.push(await scroll.evaluate(node=>({top:node.scrollTop,end:node.scrollHeight-node.clientHeight,opening:[...document.querySelectorAll('[role="status"]')].filter(item=>item.textContent?.includes('正在打开')).length})))
 }
 for(const sample of samples){assert.equal(sample.opening,0);assert.ok(sample.end-sample.top<=40,'conversation should reopen near the bottom without a visible jump')}
 assert.deepEqual(errors,[])
 console.log('Conversation reopen: message bodies load without placeholders and the bottom position remains stable across initial layout settling')
 await page.close()
},{entry:'/__conversation-reopen-jump-entry.js',vite:{plugins:[{name:'conversation-reopen-jump-fixture',resolveId(id){if(id==='/__conversation-reopen-jump-entry.js')return id},load(id){if(id==='/__conversation-reopen-jump-entry.js')return entry},configureServer(server){server.middlewares.use('/__conversation-reopen-jump',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__conversation-reopen-jump','<html><body><div id="root"></div><script type="module" src="/__conversation-reopen-jump-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})

import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {browserScenario} from './browser-scenario.mjs'
const resources=Object.fromEntries(['en','zh-CN'].map(locale=>[locale,JSON.parse(readFileSync(new URL('../src/i18n/locales/'+locale+'.json',import.meta.url),'utf8'))]))
const modes=['approval','input','sending','retrying','planning','executing','summarizing','waiting','preparing']
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {Conversation} from '/src/components/chat/Conversation.tsx'
import {ConversationSuggestions} from '/src/components/chat/ConversationSuggestions.tsx'
import {useAppStore} from '/src/store/app.ts'
import {useChatStore,emptySession} from '/src/store/chat.ts'
import {useDraftStore} from '/src/store/drafts.ts'
import {readGeneration} from '/src/lib/generation.ts'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.suggestions=[]
useAppStore.setState({activeSessionId:'owner'})
const root=createRoot(document.getElementById('root'))
window.show=mode=>{
 const blocks=mode==='empty'?[]:[{id:'original',kind:'user',at:1,text:'Original request 原文'}]
 if(['approval','input'].includes(mode))blocks.push({id:'question',kind:'question',at:1,questionId:'fixture',questions:[{id:mode==='approval'?'permission':'fixture',question:'Original question 原文'}]})
 if(['planning','executing','summarizing'].includes(mode))blocks.push({id:'team',kind:'team',at:1,runId:'fixture',team:'Original team 原文',phase:{planning:'plan',executing:'execute',summarizing:'summary'}[mode],members:[]})
 const generation=['waiting','retrying'].includes(mode)?readGeneration({request_id:'fixture',phase:mode==='retrying'?'retrying':'waiting'}):null
 const running=!['empty','idle','approval','input','sending'].includes(mode),streaming=mode==='streaming'
 useChatStore.setState({sessions:{owner:{...emptySession(),blocks,running,generation}}})
 useDraftStore.setState({submissions:{owner:mode==='sending'?[{action:'send',status:'sending'}]:[]}})
 root.render(React.createElement(React.Fragment,null,
   React.createElement(Conversation,{blocks,running,streaming,connection:'connected',readOnly:mode!=='empty'}),
   mode==='empty'?React.createElement(ConversationSuggestions,{onChoose:text=>window.suggestions.push(text)}):null))
}
window.show('empty')
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[],requests=[];page.setDefaultTimeout(10000)
 page.on('pageerror',error=>errors.push(error.message))
 await page.route('**/api/v1/**',route=>{requests.push(route.request().method());return route.fulfill({status:500,json:{error:'Unexpected request'}})})
 await page.goto(server.resolvedUrls.local[0]+'__conversation-resource-chain')
 const keys=['b2b6ca2ea1f1','8eb6b977850f','0ac196393e3f','20061c1959d0']
 const suggestions=[]
 for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  await page.evaluate(()=>window.show('empty'))
  for(const key of keys){const label=resources[locale]['ui.components.chat.Conversation.'+key];assert.ok(label);await page.getByRole('button',{name:label,exact:true}).click();suggestions.push(label)}
  assert.deepEqual(await page.evaluate(()=>window.suggestions),suggestions)
  for(const mode of modes){
   await page.evaluate(mode=>window.show(mode),mode)
   await page.getByText(resources[locale]['ui.reply.'+mode],{exact:true}).first().waitFor()
   await page.getByText('Original request 原文',{exact:true}).waitFor()
  }
  for(const mode of ['idle','streaming']){
   await page.evaluate(mode=>window.show(mode),mode)
   await page.getByText('Original request 原文',{exact:true}).waitFor()
   for(const label of modes.map(mode=>resources[locale]['ui.reply.'+mode]))await page.getByText(label,{exact:true}).waitFor({state:'hidden'})
  }
 }
 assert.deepEqual(requests,[]);assert.deepEqual(errors,[])
},{entry:'/__conversation-resource-chain-entry.js',vite:{plugins:[{name:'conversation-resource-chain',resolveId(id){if(id==='/__conversation-resource-chain-entry.js')return id},load(id){if(id==='/__conversation-resource-chain-entry.js')return entry},configureServer(server){server.middlewares.use('/__conversation-resource-chain',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__conversation-resource-chain','<html><body><div id="root" style="height:850px"></div><script type="module" src="/__conversation-resource-chain-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})

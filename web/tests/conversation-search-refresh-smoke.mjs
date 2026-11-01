import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ConversationSearch} from '/src/components/overlays/ConversationSearch.tsx'
import {useAppStore} from '/src/store/app.ts'
import {api} from '/src/lib/api.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;window.searches=[];window.reads=[]
useAppStore.setState({activeSessionId:'fixture',sessions:[{id:'fixture',title:'Original conversation',cwd:'/fixture'}],conversationSearchOpen:true})
api.searchConversation=async(id,query,options)=>{window.searches.push({id,query,through:options.through});return {ok:true,data:{session_id:window.corrupt==='session'&&window.searches.length===2?'other':id,query:window.corrupt==='query'&&window.searches.length===2?'Wrong query':query,through:window.searches.length+10,total:window.searches.length===1?1:0,results:window.searches.length===1?[{source_seq:5,source_time:1,role:'user',prefix:'',match:'Original',suffix:' immutable source',start_char:0}]:[],has_more:false,next_before:null}}}
api.readConversationMessage=async(id,seq,start)=>{window.reads.push({id,seq,start});const text='Original immutable source';return {ok:true,data:{session_id:id,source_seq:seq,source_time:1,role:'user',text,start:0,end:text.length,total_chars:text.length,previous_start:null,next_start:null}}}
createRoot(document.getElementById('root')).render(React.createElement(ConversationSearch))
`
await browserScenario(async({server,browser})=>{
 for(const language of ['en','zh-CN'])for(const corrupt of [false,'query','session']){
 const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.goto(server.resolvedUrls.local[0]+'__model-width')
 await page.evaluate(language=>window.locale(language),language)
 await page.locator('[data-conversation-search]').fill('Original')
 const results=page.getByRole('list')
 await results.getByRole('button').click()
 const reader=page.getByLabel(await page.evaluate(()=>window.copy('ui.components.overlays.ConversationSearch.c5e085837103')),{exact:true})
 await reader.getByText('Original immutable source',{exact:true}).waitFor()
 await page.evaluate(corrupt=>window.corrupt=corrupt,corrupt)
 await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.components.overlays.ConversationSearch.ab2eb6742d71')),exact:true}).click()
 await page.waitForFunction(()=>window.searches.length===2)
 if(corrupt){
  await page.getByRole('alert').filter({hasText:await page.evaluate(()=>window.copy('ui.http.invalidResponse'))}).waitFor()
  assert.equal(await reader.textContent(),'Original immutable source')
  await page.evaluate(()=>window.corrupt=false)
  await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.components.overlays.ConversationSearch.b8784c8dd563')),exact:true}).click()
 }
 await page.getByText(await page.evaluate(()=>window.copy('ui.components.overlays.ConversationSearch.a09f7ec4f5c2')),{exact:true}).waitFor()
 assert.equal(await reader.textContent(),'Original immutable source')
 assert.deepEqual(await page.evaluate(()=>window.reads),[{id:'fixture',seq:5,start:0}])
 assert.equal(await page.evaluate(()=>window.searches[1].through),undefined)
 await page.locator('[data-conversation-search]').fill('Changed query')
 await reader.waitFor({state:'detached'})
 assert.deepEqual(errors,[]);await page.close()
 }
 console.log('Conversation search refresh updates results while preserving the selected immutable source; changing query clears selection')
},{entry:'/__model-width-entry.js',vite:{plugins:[{name:'model-width',resolveId(id){if(id==='/__model-width-entry.js')return id},load(id){if(id==='/__model-width-entry.js')return entry},configureServer(server){server.middlewares.use('/__model-width',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__model-width','<html><body><div id="root"></div><script type="module" src="/__model-width-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})

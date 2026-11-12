import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {NoticeBlock} from '/src/components/chat/blocks/MessageBlocks.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import {useChatStore} from '/src/store/chat.ts'
import {permissionDecision} from '/src/lib/permissions.ts'
import '/src/styles.css'
setLocale('en')
useChatStore.getState().hydrate('fixture',{header:{id:'fixture'},events:[],usage:{},stats:{},todos:[],timeline:[],events_view:'conversation-window',interrupted:true,messages:[
 {kind:'message',role:'user',source:'goal',source_seq:1,turn:1,text:'Private goal'},
 {kind:'message',role:'tool',source_seq:2,turn:1,results:[{tool_use_id:'call',name:'FixtureTool',content:'Original output 原文',approval_decision:['allow'],approval_reviewer:'auto_review',approval_reason:'Original reason 原文'}]}
]})
const blocks=useChatStore.getState().get('fixture').blocks
const notices=blocks.filter(block=>block.kind==='notice')
notices.push({id:'original',kind:'notice',at:0,tone:'info',text:'Original notice 原文'})
window.configure=(language,dark)=>{setLocale(language);document.documentElement.classList.toggle('dark',dark);return [t('ui.store.chat.64f873358223'),t('ui.store.chat.c36db9f5daed',{p0:permissionDecision(['allow'],'auto_review','Original reason 原文'),p1:'FixtureTool'}),t('ui.store.chat.701db4b527c4'),'Original notice 原文']}
window.same=()=>useChatStore.getState().get('fixture').blocks===blocks
createRoot(document.getElementById('root')).render(React.createElement('main',{},notices.map(block=>React.createElement('section',{'data-notice':block.id,key:block.id},React.createElement(NoticeBlock,{block})))))
`
const html='<html><body><div id="root"></div><script type="module" src="/__notice-entry.js"></script></body></html>'
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage()
 const errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.goto(server.resolvedUrls.local[0]+'__notice')
 await page.waitForFunction(()=>document.querySelectorAll('[data-notice]').length===4)
 for(const width of [390,1440])for(const dark of [false,true])for(const language of ['en','zh-CN','en']){
  await page.setViewportSize({width,height:900})
  const expected=await page.evaluate(([language,dark])=>window.configure(language,dark),[language,dark])
  await page.waitForFunction(expected=>JSON.stringify([...document.querySelectorAll('[data-notice]')].map(node=>node.textContent))===JSON.stringify(expected),expected)
  assert.equal(await page.evaluate(()=>window.same()),true,'Locale changes must not rebuild stored blocks')
 }
 assert.deepEqual(errors,[])
 console.log('Notices: unchanged stored goal/approval/interruption blocks render en→zh→en, raw text preserved, both themes and widths passed')
},{entry:'/__notice-entry.js',vite:{plugins:[{name:'notice-fixture',resolveId(id){if(id==='/__notice-entry.js')return id},load(id){if(id==='/__notice-entry.js')return entry},configureServer(server){server.middlewares.use('/__notice',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__notice',html))}catch(error){next(error)}})}}]}})

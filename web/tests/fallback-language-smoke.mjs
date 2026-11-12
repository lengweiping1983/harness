import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
const resources=Object.fromEntries(['en','zh-CN'].map(locale=>[locale,JSON.parse(readFileSync(new URL('../src/i18n/locales/'+locale+'.json',import.meta.url),'utf8'))]))
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ErrorBlock} from '/src/components/chat/blocks/MessageBlocks.tsx'
import {ToolBlock} from '/src/components/chat/blocks/ToolBlock.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import {blocksFromDetail,emptySession,reduceEvent} from '/src/store/chat.ts'
import '/src/styles.css'
setLocale('en')
const initial={...emptySession(),turn:1,blocks:[{id:'tool',kind:'tool',at:0,turn:1,callId:'call',name:'fixture',status:'running'}]}
const tool=reduceEvent(initial,{type:'turn/end',turn:1,reason:'interrupted'}).blocks[0]
const runtime=reduceEvent(emptySession(),{type:'error'}).blocks[0]
const terminal=reduceEvent(emptySession(),{type:'turn/end',reason:{kind:'error'}}).blocks[0]
const copy={key:'ui.run.incomplete',params:{reason:'Original reason 原文'}}
const error={code:'run/incomplete',message:'Original diagnostic 原文',copy}
const localized=reduceEvent(emptySession(),{type:'error',...error}).blocks[0]
const restored=blocksFromDetail({header:{id:'fixture'},messages:[],events:[{type:'turn/end',seq:1,data:{turn:1,reason:{kind:'error',error:{...error,copy:undefined,details:{copy}}}}}],usage:{},stats:{},todos:[],timeline:[]}).find(block=>block.kind==='error')
const original={...tool,id:'original',outputKey:undefined,output:'Original output 原文'}
window.configure=(language,dark)=>{setLocale(language);document.documentElement.classList.toggle('dark',dark);return [runtime.messageKey,terminal.messageKey]}
window.same=()=>tool.output===undefined&&tool.outputKey==='ui.store.chat.a57ff6bf2376'&&runtime.message===''&&terminal.message===''
createRoot(document.getElementById('root')).render(React.createElement('main',{},
 React.createElement('section',{'data-fallback':'tool'},React.createElement(ToolBlock,{block:tool,defaultOpen:true})),
 React.createElement('section',{'data-fallback':'runtime'},React.createElement(ErrorBlock,{block:runtime})),
 React.createElement('section',{'data-fallback':'terminal'},React.createElement(ErrorBlock,{block:terminal})),
 React.createElement('section',{'data-fallback':'original'},React.createElement(ToolBlock,{block:original,defaultOpen:true})),
 React.createElement('section',{'data-fallback':'localized'},React.createElement(ErrorBlock,{block:localized})),
 React.createElement('section',{'data-fallback':'restored'},React.createElement(ErrorBlock,{block:restored}))))
`
const html='<html><body><div id="root"></div><script type="module" src="/__fallback-entry.js"></script></body></html>'
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage()
 const errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.goto(server.resolvedUrls.local[0]+'__fallback')
 await page.waitForFunction(()=>document.querySelectorAll('[data-fallback]').length===6)
 for(const width of [390,1440])for(const dark of [false,true])for(const language of ['en','zh-CN','en']){
  await page.setViewportSize({width,height:900})
  const keys=await page.evaluate(([language,dark])=>window.configure(language,dark),[language,dark])
  assert.deepEqual(keys,['ui.store.chat.13a46616e16f','ui.lib.turnOutcome.ac8aaa680b6c'])
  const expected=['ui.store.chat.a57ff6bf2376',...keys].map(key=>{assert.ok(resources[language][key]);return resources[language][key]}).concat('Original output 原文',...Array(2).fill(resources[language]['ui.run.incomplete'].replace('{reason}','Original reason 原文')))
  await page.waitForFunction(expected=>[...document.querySelectorAll('[data-fallback]')].every((node,index)=>node.textContent.includes(expected[index])),expected)
  assert.equal(await page.evaluate(()=>window.same()),true,'Language switches must preserve stored fallback identity')
 }
 assert.deepEqual(errors,[])
 console.log('Fallbacks: real ToolBlock/ErrorBlock render stored interruption and unknown failures en→zh→en, preserve original output, both themes and widths passed')
},{entry:'/__fallback-entry.js',vite:{plugins:[{name:'fallback-fixture',resolveId(id){if(id==='/__fallback-entry.js')return id},load(id){if(id==='/__fallback-entry.js')return entry},configureServer(server){server.middlewares.use('/__fallback',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__fallback',html))}catch(error){next(error)}})}}]}})

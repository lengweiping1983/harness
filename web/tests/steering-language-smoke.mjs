import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
const resources=Object.fromEntries(['en','zh-CN'].map(locale=>[locale,JSON.parse(readFileSync(new URL('../src/i18n/locales/'+locale+'.json',import.meta.url),'utf8'))]))
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import '/@vite/client'
import React from 'react'
import {createRoot} from 'react-dom/client'
import {UserBlock} from '/src/components/chat/blocks/MessageBlocks.tsx'
import {emptySession,reduceEvent,blocksFromDetail} from '/src/store/chat.ts'
import {SteeringDock} from '/src/components/chat/SteeringDock.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t;setLocale('en')
createRoot(document.getElementById('root')).render(React.createElement(SteeringDock,{sessionId:'fixture',receipts:[{request_id:'request',turn:1,accepted_seq:7,status:'unapplied',text:'Original 原文'}],onRestored:()=>{throw Error('Unexpected restore')}}))

const fixtures=['pending','delivered','applied','unapplied'].flatMap(mode=>{
 const status=mode==='delivered'?'pending':mode
 const receipt={request_id:mode,turn:1,accepted_seq:7,accepted_at:1,applied_seq:status==='applied'?8:null,status,text:'Original message 原文 '+mode,reason:'Original reason 原文',text_truncated:false,team_delivered:mode==='delivered',delivery_seq:mode==='delivered'?9:null}
 const live=reduceEvent({...emptySession(),turn:1},{type:'steer/state',receipt}).blocks[0]
 const history=blocksFromDetail({header:{id:'fixture'},events:[],messages:[{role:'user',source_seq:7,text:receipt.text,steering_status:status,steering_team_delivered:receipt.team_delivered,steering_delivery_seq:receipt.delivery_seq,steering_reason:receipt.reason}],usage:{},stats:{},todos:[],timeline:[]})[0]
 return [['live',live],['history',history]].map(([source,block])=>({mode,source,block}))
})
window.snapshots=()=>JSON.stringify(fixtures)
for(const fixture of fixtures){const root=document.createElement('section');root.id=fixture.source+'-'+fixture.mode;document.body.append(root);createRoot(root).render(React.createElement(UserBlock,{block:fixture.block,hideActions:true}))}

`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage();let reads=0;const errors=[];page.on('pageerror',error=>{errors.push(error.message);console.error(error.message)})
 await page.route('**/api/v1/sessions/fixture/steering/request',route=>{reads++;return route.fulfill({json:{receipt:null}})})
 await page.goto(server.resolvedUrls.local[0]+'__steering');await page.getByRole('button').click();const snapshots=await page.evaluate(()=>window.snapshots())
 for(const locale of ['en','zh-CN','en']) for(const theme of ['light','dark']) for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
  for(const mode of ['pending','delivered','applied','unapplied'])for(const source of ['live','history']){
   const card=page.locator('#'+source+'-'+mode),key=mode==='delivered'?'ui.steering.teamDelivered':'ui.messageEdit.steering.'+mode
   await card.getByText(resources[locale][key],{exact:true}).waitFor()
   await card.getByText('Original message 原文 '+mode,{exact:true}).waitFor()
   assert.equal(await card.getByTitle('Original reason 原文',{exact:true}).count(),1)
  }
  assert.equal(await page.evaluate(()=>window.snapshots()),snapshots)
  const expected=await page.evaluate(()=>window.copy('ui.components.chat.SteeringDock.d590a5909a83'))
  await page.getByText(expected,{exact:true}).waitFor();assert.equal(reads,1);assert.ok(await page.getByText('Original 原文',{exact:true}).isVisible())
 }
 assert.deepEqual(errors,[]);await page.close();console.log('Steering error: same saved state changes language without refetching or restoring; original text preserved')

},{entry:'/__steering-entry.js',vite:{plugins:[{name:'steering-language-fixture',resolveId(id){if(id==='/__steering-entry.js')return id},load(id){if(id==='/__steering-entry.js')return entry},configureServer(server){server.middlewares.use('/__steering',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__steering','<html><body><div id="root"></div><script type="module" src="/__steering-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})

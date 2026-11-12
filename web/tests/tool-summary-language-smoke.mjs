import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ToolBlock} from '/src/components/chat/blocks/ToolBlock.tsx'
import {blocksFromDetail,emptySession,reduceEvent} from '/src/store/chat.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t
const base={kind:'tool',at:0,turn:1,status:'ok',input:{},output:'Original output 原文'}
const pixel='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='
const reasons=['stdin_read','inferred_idle','timeout','session_exit','cancelled','unknown']
const blocks=[{...base,id:'command',callId:'command',name:'bash',input:{command:'printf "Original command 原文"; '.repeat(80)},durationMs:1250,display:{exit_code:7}},...[1,2].flatMap(count=>['ok','error'].map((status,i)=>({...base,id:'images'+count+'-'+i,callId:'images'+count+'-'+i,name:'read_images',status,output:Array(count).fill(pixel).join('\\n')}))),...reasons.map((reason,i)=>({...base,id:'wait'+i,callId:'wait'+i,name:'terminal_send',display:{terminal_wait:{reason},exit_code:7}}))]
blocks.push({...base,id:'command',callId:'other-command',name:'bash',durationMs:1250,display:{exit_code:7},fixtureScope:'other'})
const restored=blocksFromDetail({header:{id:'fixture'},interrupted:true,events:[{type:'tool/call',seq:1,data:{call_id:'restore',name:'fixture',turn:1,status:'running'}}],messages:[],usage:{},stats:{},todos:[],timeline:[]}).find(block=>block.kind==='tool')
const state={...emptySession(),turn:1,blocks:[{id:'settled',kind:'tool',at:0,turn:1,callId:'settled',name:'fixture',status:'running'}]}
const patch=reduceEvent(state,{type:'turn/end',turn:1,reason:'interrupted'})
blocks.push({...restored,id:'fallback-restore',callId:'restore'}, {...patch.blocks.find(block=>block.kind==='tool'),id:'fallback-settled'})
window.blocks=blocks
createRoot(document.getElementById('root')).render(React.createElement('main',null,...blocks.map(block=>React.createElement('section',{key:block.callId,'data-summary':block.fixtureScope?'other-command':block.id},React.createElement(ToolBlock,{block,defaultOpen:true})))))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[],requests=[]
 page.on('pageerror',error=>errors.push(error.message))
 await page.route('**/api/v1/**',route=>{requests.push(route.request().url());return route.fulfill({status:500,json:{error:'Unexpected request'}})})
 await page.goto(server.resolvedUrls.local[0]+'__tool-summary')
 const details=await page.locator('[aria-controls]').evaluateAll(nodes=>nodes.map(node=>({id:node.getAttribute('aria-controls'),exists:!!document.getElementById(node.getAttribute('aria-controls'))})))
 assert.equal(new Set(details.map(row=>row.id)).size,details.length,'Repeated source block IDs must retain separate DOM controls')
 assert.ok(details.every(row=>row.exists))
 for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  for(const [id,key,values] of [['command','ui.toolBlock.duration',{duration:'1.25s'}],['command','ui.toolBlock.exitCode',{code:7}],...[1,2].flatMap(count=>[[`images${count}-0`,'ui.toolBlock.imagesViewed',{count}],[`images${count}-1`,'ui.toolBlock.imagesPartial',{count}]])]){
   const expected=await page.evaluate(({key,values})=>window.copy(key,values),{key,values})
   await page.locator('[data-summary="'+id+'"]').getByText(expected,{exact:true}).waitFor()
   if(locale==='en')assert.ok(expected.includes(': '))
  }
  for(const [index,suffix] of ['88fa796f1183','84dbc05a5258','11c0c3f07e95','b6e712c3287b','4c911b2b45c8','3153e9aea0fb'].entries()){
   const expected=await page.evaluate(suffix=>window.copy('ui.lib.commandResults.'+suffix),suffix)
   await page.locator('[data-summary="wait'+index+'"]').getByText(expected,{exact:true}).waitFor()
   if(locale==='en')assert.ok(expected.includes('. Waiting'))
  }
  await page.locator('[data-summary="command"]').getByText('Original output 原文',{exact:true}).waitFor()
  const summaryGeometry=await page.locator('[data-summary="command"] button[aria-controls]').first().evaluate(node=>({
   right:node.getBoundingClientRect().right,arrow:node.querySelector(':scope > svg:last-child').getBoundingClientRect().right,
  }))
  assert.ok(summaryGeometry.right-summaryGeometry.arrow<=6,'Tool summary uses its full row width and keeps the disclosure arrow at the detail right edge')
  for(const [id,key] of [['fallback-restore','ui.store.chat.eda14e9f81aa'],['fallback-settled','ui.store.chat.a57ff6bf2376']]){
   await page.locator('[data-summary='+id+']').getByText(await page.evaluate(key=>window.copy(key),key),{exact:true}).waitFor()
  }
  assert.equal(await page.evaluate(()=>window.blocks[0].display.exit_code),7)
  for(const count of [1,2])for(const state of [0,1])assert.equal(await page.locator('[data-summary="images'+count+'-'+state+'"] img').count(),count)
 }
 assert.deepEqual(requests,[]);assert.deepEqual(errors,[])
 console.log('Tool summaries: complete duration/exit/image templates, original evidence and image counts preserved en→zh→en across themes and widths')
},{entry:'/__tool-summary-entry.js',vite:{plugins:[{name:'tool-summary-fixture',resolveId(id){if(id==='/__tool-summary-entry.js')return id},load(id){if(id==='/__tool-summary-entry.js')return entry},configureServer(server){server.middlewares.use('/__tool-summary',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__tool-summary','<html><body><div id="root"></div><script type="module" src="/__tool-summary-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})

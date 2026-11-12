import assert from 'node:assert/strict'
import {mkdir} from 'node:fs/promises'
import {fileURLToPath} from 'node:url'
import {browserScenario} from './browser-scenario.mjs'

const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ToolBlock} from '/src/components/chat/blocks/ToolBlock.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t
const base={kind:'tool',at:1,status:'ok'}
const blocks=[
 {...base,id:'file',callId:'file',name:'write',durationMs:6,input:{path:'bow_animation.svg',content:'<svg><title>Original SVG 原文</title></svg>'},output:{path:'bow_animation.svg',bytes:55}},
 {...base,id:'command',callId:'command',name:'bash',durationMs:165,input:{command:'python3 verify.py'},display:{exit_code:0},output:'XML OK 原文'},
 {...base,id:'running',callId:'running',name:'bash',status:'running',input:{command:'python3 live.py'},progress:{seq:1,stdout:'Live output 原文',stderr:'',stdoutDropped:0,stderrDropped:0}},
 {...base,id:'failed',callId:'failed',name:'bash',status:'error',input:{command:'python3 failure.py'},display:{exit_code:2},output:'Original failure 原文'}
]
createRoot(document.getElementById('root')).render(React.createElement('main',{className:'mx-auto max-w-5xl p-4'},...blocks.map(block=>React.createElement('div',{key:block.id,'data-fixture':block.id},React.createElement(ToolBlock,{block,defaultOpen:true})))))
`

await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[],requests=[]
 page.on('pageerror',error=>errors.push(error.message))
 await page.route('**/api/v1/**',route=>{requests.push(route.request().url());return route.fulfill({status:500,json:{error:'Unexpected request'}})})
 await page.goto(server.resolvedUrls.local[0]+'__tool-details-layout')
 const running=page.locator('[data-fixture="running"] > div > button[aria-controls]')
 await running.click()
 await page.locator('[data-fixture="running"] [data-tool-detail]').getByText('Live output 原文',{exact:true}).waitFor()
 for(const locale of ['en','zh-CN','en'])for(const [dark,width] of [[false,390],[true,1440],[false,1440]]){
  await page.setViewportSize({width,height:1100})
  await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  const input=await page.evaluate(()=>window.copy('ui.components.chat.blocks.ToolBlock.2087c777c06f'))
  await page.locator('[data-fixture="file"]').getByRole('heading',{name:input,exact:true}).waitFor()
  const geometry=await page.locator('[data-tool-detail]').evaluateAll(cards=>cards.map(card=>{
   const header=card.querySelector(':scope > header'),body=header.nextElementSibling,footer=card.querySelector(':scope > footer'),style=getComputedStyle(card),rect=card.getBoundingClientRect()
   const box=node=>{const rect=node.getBoundingClientRect();return {top:rect.top,bottom:rect.bottom,left:rect.left,right:rect.right}}
   return {card:box(card),header:box(header),body:box(body),footer:box(footer),borders:['Top','Right','Bottom','Left'].map(side=>parseFloat(style['border'+side+'Width'])),padding:parseFloat(getComputedStyle(body).paddingLeft),
    headerSurface:getComputedStyle(header).backgroundColor,bodySurface:getComputedStyle(body).backgroundColor,sections:[...body.querySelectorAll(':scope > section')].map(box),
    json:[...body.querySelectorAll('[data-json-view]')].map(node=>getComputedStyle(node).borderTopWidth),overflow:card.scrollWidth-card.clientWidth,height:rect.height}
  }))
  assert.equal(geometry.length,4)
  for(const row of geometry){
   assert.ok(row.borders.every(width=>width>=1),'Expanded details must have a complete boundary')
   assert.ok(row.header.top>=row.card.top&&row.footer.bottom<=row.card.bottom,'Header and footer must stay inside the card')
   assert.ok(row.body.top>=row.header.bottom&&row.footer.top>=row.body.bottom,'Evidence must be between metadata and footer')
   assert.ok(row.padding>=12,'Evidence must have readable inner padding')
   assert.notEqual(row.headerSurface,row.bodySurface,'Metadata header must have its own surface')
   assert.ok(row.sections.every(section=>section.top>=row.body.top&&section.bottom<=row.body.bottom))
   assert.ok(row.json.every(border=>border==='0px'),'JSON must not add a second frame inside its section')
   assert.ok(row.overflow<=1,'Narrow cards must not overflow horizontally')
  }
  const separation=await page.evaluate(()=>{
   const card=document.querySelector('[data-fixture="file"] [data-tool-detail]').getBoundingClientRect()
   const next=document.querySelector('[data-fixture="command"] > div > button').getBoundingClientRect()
   return next.top-card.bottom
  })
  assert.ok(separation>=8,'Next tool summary must begin after the previous card has visibly ended')
  for(const id of ['file','command','running','failed']){
   const card=page.locator('[data-fixture="'+id+'"] [data-tool-detail]')
   assert.equal(await card.locator(':scope > footer button').count(),1)
   assert.equal(await card.locator(':scope > header button').count(),0)
  }
  await page.getByText('Live output 原文',{exact:true}).waitFor()
  await page.getByText('Original failure 原文',{exact:true}).waitFor()
  if(locale==='zh-CN'&&!dark&&width===1440){
   const directory=new URL('../../output/',import.meta.url)
   await mkdir(directory,{recursive:true})
   await page.screenshot({path:fileURLToPath(new URL('tool-details-hierarchy.png',directory)),fullPage:true})
  }
 }
 await page.locator('[data-fixture="command"] > div > button[aria-controls]').click()
 assert.equal(await page.locator('[data-fixture="command"] [data-tool-detail]').count(),0)
 assert.equal(await page.locator('[data-fixture="file"] [data-tool-detail]').count(),1)
 assert.deepEqual(errors,[]);assert.deepEqual(requests,[])
 console.log('Tool detail hierarchy: bounded cards, metadata first, separate evidence sections, learning footer, adjacent boundaries and live output passed en→zh→en across themes and widths')
},{entry:'/__tool-details-layout-entry.js',vite:{plugins:[{name:'tool-details-layout-fixture',resolveId(id){if(id==='/__tool-details-layout-entry.js')return id},load(id){if(id==='/__tool-details-layout-entry.js')return entry},configureServer(server){server.middlewares.use('/__tool-details-layout',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__tool-details-layout','<html><body><div id="root"></div><script type="module" src="/__tool-details-layout-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})

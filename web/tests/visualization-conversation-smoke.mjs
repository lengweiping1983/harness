import assert from 'node:assert/strict'
import {spawnSync} from 'node:child_process'
import {browserScenario} from './browser-scenario.mjs'
import {fixturePython} from './python-fixture.mjs'

const result=spawnSync(fixturePython,['-m','tests.widget_visual_fixture'],{cwd:new URL('../..',import.meta.url),encoding:'utf8',env:{...process.env,PYTHONUTF8:'1'}})
assert.equal(result.status,0,result.stderr)
const fixture=JSON.parse(result.stdout)
assert.equal(fixture.reason,'completed',fixture.error)
const deliveries=fixture.tools.filter(row=>row.display?.widget)
const entry=`
import React,{useState} from 'react'
import {createRoot} from 'react-dom/client'
import {Conversation} from '/src/components/chat/Conversation.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
const deliveries=${JSON.stringify(deliveries)}
useAppStore.setState({activeSessionId:'visual-conversation-fixture'})
window.locale=setLocale
function App(){
 const [phase,setPhase]=useState('pending');window.phase=setPhase
 const start=Date.now()-1000
 const blocks=[{kind:'user',id:'user',sourceSeq:1,turn:1,at:start,text:'HTML preview request'},
 {kind:'thinking',id:'thought',turn:1,at:start,text:'Original thought'},
 {kind:'tool',id:'read',callId:'read-call',turn:1,at:start,name:'read',status:'ok',input:{path:'file'},output:'Original read'},
 ...deliveries.map(row=>({kind:'tool',id:row.call_id,callId:row.call_id,turn:1,at:start,name:'show_widget',status:phase==='pending'?'running':'ok',input:{title:row.display.widget.title},display:phase==='pending'?undefined:row.display,output:phase==='pending'?undefined:row.results[0].content})),
 ...(phase==='pending'?[]:[
 {kind:'assistant',id:'inline-html',turn:1,sourceSeq:15,at:start,text:${JSON.stringify('```html\n<button onclick="this.textContent=\'Inline clicked\'">Inline interactive</button>\n```')}},
 {kind:'assistant',id:'inline-table',turn:1,sourceSeq:16,at:start,text:${JSON.stringify('```csv\nName,Value\nEarlier preview,10\n```')}}]),
 {kind:'assistant',id:'answer',turn:1,sourceSeq:19,at:start,text:'Use the controls above.'}]
 const activity={turn:1,startSeq:0,startedAt:start,...(phase==='complete'?{reason:'completed',endedAt:Date.now(),endSeq:20,finalResponseSeq:19}:{})}
 return React.createElement('div',{style:{height:1500,display:'flex'}},React.createElement(Conversation,{blocks,turns:{1:activity},running:phase!=='complete',streaming:phase!=='complete',connection:'open',readOnly:true}))
}
createRoot(document.getElementById('root')).render(React.createElement(App))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[]
 page.on('pageerror',error=>errors.push(error.message))
 await page.setViewportSize({width:1100,height:1800})
 await page.route('**/api/v1/sessions/*/turns/*/completion*',route=>route.fulfill({json:{turn:1,end_seq:20,final_response_seq:19,response_seq:19,ended_at:0,feedback:null,delivery:{run_id:'fixture',start_seq:0,tool_executions:0,failed_tools:0,unknown_tools:0,artifacts:[],artifact_count:0,verification:'unverified',checks:[]},hooks:{total:0,runs:0,blocked:0,unsuccessful:0,counts:{},duration_ms:0,rows:[],offset:0,next_offset:null}}}))
 await page.goto(server.resolvedUrls.local[0]+'__visual-conversation')
 await page.locator('[data-tool-call-id="widget-3"] [role="status"]').waitFor()
 assert.equal(await page.locator('iframe').count(),0)
 await page.evaluate(()=>window.phase('ready'))
 const frame=page.frameLocator('iframe[title="HTML demo"]')
 const ready='[aria-busy="false"] > iframe[title="HTML demo"]'
 await page.locator(ready).waitFor()
 await frame.getByRole('button',{name:'运行模拟 · 0',exact:true}).click()
 await frame.getByRole('button',{name:'运行模拟 · 1',exact:true}).waitFor()
 const inline=page.frameLocator('[data-row-id="inline-html"] iframe')
 await inline.getByRole('button',{name:'Inline interactive',exact:true}).click()
 await page.locator('[data-row-id="inline-table"] tbody').getByText('Earlier preview',{exact:true}).waitFor()
 await page.evaluate(()=>window.phase('complete'))
 await page.locator('[data-row-id="activity-turn-1"] button[aria-expanded="false"]').waitFor()
 await page.locator(ready).waitFor()
 await frame.getByRole('button',{name:'运行模拟 · 1',exact:true}).waitFor()
 await inline.getByRole('button',{name:'Inline clicked',exact:true}).waitFor()
 await page.locator('[data-row-id="inline-table"] tbody').getByText('Earlier preview',{exact:true}).waitFor()
 for(const locale of ['en','zh-CN','en'])for(const [dark,width] of [[false,390],[true,1440]]){
  await page.setViewportSize({width,height:1800})
  await page.evaluate(async({locale,dark})=>{await window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  await page.locator(ready).waitFor()
  await frame.getByRole('button',{name:'运行模拟 · 1',exact:true}).waitFor()
  await inline.getByRole('button',{name:'Inline clicked',exact:true}).waitFor()
  await page.locator('[data-row-id="inline-table"] tbody').getByText('Earlier preview',{exact:true}).waitFor()
  await frame.locator('#workers').evaluate(node=>{node.value='8';node.dispatchEvent(new Event('input',{bubbles:true}))})
  assert.equal(await frame.locator('#queued').textContent(),'16')
  await page.locator('.widget-host > svg').waitFor()
  const widget=page.locator('[data-tool-call-id="widget-3"]')
  const disclosure=widget.locator(':scope > button').first()
  const detailId=await disclosure.getAttribute('aria-controls')
  assert.ok(detailId)
  assert.equal(await disclosure.evaluate(button=>{
   const title=button.querySelector('span[title]:last-of-type'),arrow=button.querySelector('.lucide-chevron-right')
   const gap=title&&arrow?arrow.getBoundingClientRect().left-title.getBoundingClientRect().right:-1
   return gap>=0&&gap<=12
  }),true,'The disclosure arrow stays beside the visualization title')
  await disclosure.focus()
  await page.keyboard.press('Enter')
  await widget.locator('button[aria-expanded="true"]').first().waitFor()
  assert.equal(await widget.evaluate((node,id)=>{
   const detail=node.querySelector(`[id="${id}"]`),preview=node.querySelector('figure')
   if(!detail||!preview||detail.getBoundingClientRect().bottom>preview.getBoundingClientRect().top)return false
   const style=getComputedStyle(detail)
   return ['borderTopWidth','borderRightWidth','borderBottomWidth','borderLeftWidth'].every(side=>parseFloat(style[side])>0)
  },detailId),true,'Expanded details appear above the preview without overlapping it')
  await frame.getByRole('button',{name:'运行模拟 · 1',exact:true}).waitFor()
  await disclosure.focus()
  await page.keyboard.press('Space')
  await widget.locator(`[id="${detailId}"]`).waitFor({state:'detached'})
  assert.equal(await widget.locator(`[id="${detailId}"]`).count(),0)
  await frame.getByRole('button',{name:'运行模拟 · 1',exact:true}).waitFor()
  const activity=page.locator('[data-row-id="activity-turn-1"] button')
  await activity.click()
  await page.locator('[data-tool-run-turn="1"] button[aria-expanded="false"]').waitFor()
  await page.locator(ready).waitFor()
  await frame.getByRole('button',{name:'运行模拟 · 1',exact:true}).waitFor()
  await activity.click()
  await page.locator(ready).waitFor()
  await frame.getByRole('button',{name:'运行模拟 · 1',exact:true}).waitFor()
  assert.equal(await page.locator('[data-tool-call-id="widget-3"]').count(),1)
 }
 await page.reload()
 await page.locator('[data-tool-call-id="widget-3"] [role="status"]').waitFor()
 await page.evaluate(()=>window.phase('complete'))
 await page.locator(ready).waitFor()
 await frame.getByRole('button',{name:'运行模拟 · 0',exact:true}).waitFor()
 assert.deepEqual(errors,[])
 console.log('Actual Conversation: pending, completed and restored SVG/HTML deliveries survive both disclosure layers; interaction state survives completion, languages, themes and widths')
},{entry:'/__visual-conversation-entry.js',vite:{plugins:[{name:'visual-conversation-fixture',resolveId(id){if(id==='/__visual-conversation-entry.js')return id},load(id){if(id==='/__visual-conversation-entry.js')return entry},configureServer(server){server.middlewares.use('/__visual-conversation',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__visual-conversation','<html><body><div id="root"></div><script type="module" src="/__visual-conversation-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})

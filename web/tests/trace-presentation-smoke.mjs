import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const rows=Array.from({length:15},(_,index)=>{
 const request=[0,3,6,10].includes(index)
 return {id:(request?'request:':'tool:')+index,kind:request?'request':'tool',time:Date.UTC(2026,9,28,7,0,index),order:index,name:request?'Original model':'Original tool',status:request?'completed':'done',
  ...(request?{request:{id:'trace-'+index,request_id:'req-'+index,purpose:'agent'}}:index===1?{trace_id:'trace-0'}:{})}
})
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ExecutionTracePanel} from '/src/components/inspector/ExecutionTracePanel.tsx'
import {api} from '/src/lib/api.ts'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;setLocale('en');window.reads=0;window.detailReads=0
api.executionTrace=async()=>{window.reads++;return {ok:true,data:{entries:${JSON.stringify(rows)},has_more:false,next_after:null,context_missing:false,session_id:'owner'}}}
api.requestTrace=async(_session,id)=>{window.detailReads++;return {ok:true,data:{request:{id,session_id:'owner',request_id:'req-internal',model:'Original model',endpoint:'Original endpoint',status:'completed',body_bytes:2,context_checkpoint:{event_seq:34,event_count:35,surface_nodes:6,system_seq:2,request_header_seq:3,compaction_summary_seq:null}},messages:[{index:0,role:'system',chars:6,preview:'System'},{index:1,role:'user',chars:4,preview:'User'}],parameters:{},tool_names:['Original tool'],tool_definitions:[{index:0,name:'Original tool',description:'Original description 原文'}],composition:{normalized_bytes:2,groups:[],largest_inputs:[],largest_tools:[{index:0,name:'Original tool',description:'Original description 原文',bytes:3334}]},cache_comparison:null}}}
api.requestTracePart=async()=>({ok:true,data:{text:'{}',offset:0,total_chars:2,next_offset:null}})
createRoot(document.getElementById('root')).render(React.createElement(ExecutionTracePanel,{sessionId:'owner'}))
`

await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[]
 page.on('pageerror',error=>errors.push(error.message))
 await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
 await page.goto(server.resolvedUrls.local[0]+'__trace-presentation')
 const refresh=page.locator('section.refresh-action-container nav button.refresh-action')
 await refresh.waitFor()
 assert.notEqual(await refresh.locator('.refresh-action-label').evaluate(node=>getComputedStyle(node).display),'none','Wide trace header shows Refresh next to the icon')
 await page.locator('#root').evaluate(node=>{node.style.width='280px'})
 assert.equal(await refresh.locator('.refresh-action-label').evaluate(node=>getComputedStyle(node).display),'none','Narrow trace header shows only the icon')
 await page.locator('#root').evaluate(node=>{node.style.width=''})
 const hour=page.locator('[data-trace-hour]'),summary=hour.locator(':scope > summary')
 await hour.waitFor()
 assert.equal(await page.getByText('Model requests, tool calls and subagent activity, grouped by hour. Each group initially shows 10 entries.').count(),0)
 assert.equal(await hour.locator('[data-trace-id]').count(),10)
 const request=hour.locator('[data-trace-kind="request"]').first(),tool=hour.locator('[data-trace-kind="tool"]').first()
 await request.getByRole('img',{name:'Model request 1'}).waitFor()
 await hour.getByRole('img',{name:'Model request 2'}).waitFor()
 assert.notEqual(await request.locator('span.rounded').evaluate(node=>getComputedStyle(node).backgroundColor),await tool.locator('span.rounded').evaluate(node=>getComputedStyle(node).backgroundColor))
 const toolName=tool.getByText('Original tool',{exact:true})
 assert.equal(await toolName.locator('xpath=..').getAttribute('tabindex'),'0','Tool names can show descriptions on keyboard focus')
 await toolName.hover()
 await page.waitForFunction(()=>window.detailReads===1)
 await page.clock.runFor(250)
 await page.getByRole('tooltip').getByText('Original description 原文').waitFor()
 const more=hour.getByRole('button',{name:'Show more'})
 assert.equal(await more.evaluate(node=>node.classList.contains('activity-summary')),true)
 await more.click()
 assert.equal(await hour.locator('[data-trace-id]').count(),15)
 await hour.getByRole('img',{name:'Model request 4'}).waitFor()
 await summary.click()
 assert.equal(await request.isVisible(),false)
 await page.evaluate(async()=>window.locale('zh-CN'))
 assert.equal(await request.isVisible(),false)
 await summary.click()
 await request.getByRole('img',{name:'第 1 次模型请求'}).waitFor()
 await page.evaluate(async()=>window.locale('en'))
 await request.getByRole('img',{name:'Model request 1'}).waitFor()
 assert.equal(await page.evaluate(()=>window.reads),1)
 await hour.getByRole('button',{name:'View request details'}).nth(1).click()
 await page.locator('.request-trace-detail').getByRole('img',{name:'Model request 2'}).waitFor()
 await page.locator('.request-trace-detail .request-trace-disclosure').first().locator('summary').click()
 const detailName=page.locator('.request-trace-detail [data-tool-name]').first()
 await detailName.hover()
 await page.clock.runFor(250)
 const nameBox=await detailName.boundingBox(),tipBox=await page.getByRole('tooltip').boundingBox()
 assert.ok(nameBox&&tipBox&&(Math.abs(tipBox.x-nameBox.x-nameBox.width-6)<8||Math.abs(tipBox.x+tipBox.width+6-nameBox.x)<8),'Tool description is positioned next to the name, not the full-width row')
 const checkpoint=page.locator('.request-trace-disclosure').filter({has:page.getByText('Conversation before this request',{exact:true})})
 await checkpoint.locator('summary').click()
 await checkpoint.getByText('When this request was sent, the conversation had 35 records and its current content was divided into 6 parts.').waitFor()
 assert.equal(await checkpoint.getByText('on the right',{exact:false}).count(),0)
 await detailName.locator('xpath=../..').click()
 await page.locator('.request-trace-reader').getByText('Original tool',{exact:true}).waitFor()
 await checkpoint.getByRole('button',{name:'Request body'}).click()
 await page.locator('.request-trace-reader').getByText('Request body',{exact:true}).waitFor()
 assert.equal(await page.getByText('req-internal').count(),0)
 await page.getByRole('button',{name:'Back to request and tool log'}).waitFor()
 await page.locator('.request-trace-reader').getByText('Request body',{exact:true}).waitFor()
 await page.evaluate(()=>window.locale('zh-CN'))
 await page.locator('.request-trace-detail').getByRole('img',{name:'第 2 次模型请求'}).waitFor()
 await page.getByRole('button',{name:'返回请求与工具日志'}).waitFor()
 await page.locator('.request-trace-reader').getByText('请求体',{exact:true}).waitFor()
 await page.evaluate(()=>window.locale('en'))
 const messages=page.locator('.request-trace-disclosure').filter({has:page.getByText('Message order (2)',{exact:true})})
 await messages.locator('summary').click()
 await messages.getByRole('button',{name:/System/}).waitFor()
 await messages.locator('summary').click()
 assert.equal(await messages.locator('li button').first().isVisible(),false)
 assert.deepEqual(errors,[])
 await page.close()
 console.log('Execution trace: collapsible hours, request numbering, distinct types and shared show-more style survive en/zh/en')
},{entry:'/__trace-presentation-entry.js',vite:{plugins:[{name:'trace-presentation-fixture',resolveId(id){if(id==='/__trace-presentation-entry.js')return id},load(id){if(id==='/__trace-presentation-entry.js')return entry},configureServer(server){server.middlewares.use('/__trace-presentation',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__trace-presentation','<!doctype html><html><body><div id="root"></div><script type="module" src="/__trace-presentation-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})

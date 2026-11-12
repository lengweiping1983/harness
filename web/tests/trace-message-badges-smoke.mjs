import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const roles=['system','developer','user','assistant','tool','constructor']
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ExecutionTracePanel} from '/src/components/inspector/ExecutionTracePanel.tsx'
import {api} from '/src/lib/api.ts'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;setLocale('en');window.reads=[]
api.executionTrace=async()=>({ok:true,data:{session_id:'owner',entries:[
 {id:'request:1',kind:'request',time:1,order:0,name:'Example model',status:'completed',request:{id:'related-request',request_id:'internal-id',purpose:'agent'}},
 {id:'tool:1',kind:'tool',time:2,order:1,name:'read',status:'done',trace_id:'related-request',description:'Read a file'},
],has_more:false,next_after:null,context_missing:false}})
api.requestTrace=async(session,id)=>{window.reads.push({session,id});return {ok:true,data:{
 request:{id,session_id:session,request_id:'internal-id',model:'Example model',endpoint:'Example endpoint',status:'completed',body_bytes:2},
 messages:${JSON.stringify(roles)}.map((role,index)=>({index,role,chars:12,preview:'Original 原文 '+role})),
 parameters:{},tool_names:[],tool_definitions:[],composition:{normalized_bytes:2,groups:[],largest_inputs:[],largest_tools:[]},cache_comparison:null,
}}}
api.requestTracePart=async(_session,_trace,part,index)=>{const text=part==='message'?'Original 原文 '+${JSON.stringify(roles)}[index]:'{}';return {ok:true,data:{text,offset:0,total_chars:Array.from(text).length,next_offset:null}}}
createRoot(document.getElementById('root')).render(React.createElement('main',{className:'min-h-screen bg-surface p-3 text-ink'},React.createElement(ExecutionTracePanel,{sessionId:'owner'})))
`

await browserScenario(async({server,browser})=>{
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[]
 page.on('pageerror',error=>errors.push(error.message))
 await page.goto(server.resolvedUrls.local[0]+'__trace-badges')
 const tool=page.locator('[data-trace-kind="tool"]')
 const matrix=async check=>{
  for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
   await page.setViewportSize({width,height:1000})
   await page.evaluate(async({locale,dark})=>{await window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
   await check(locale,dark,width)
   assert.equal(await page.locator('body').evaluate(node=>node.scrollWidth<=innerWidth+1),true,'Trace badges and navigation must fit narrow screens')
  }
 }
 await matrix(async locale=>{
  await tool.getByRole('button',{name:locale==='en'?'View related request':'查看关联请求',exact:true}).waitFor()
  assert.equal(await tool.getByText(locale==='en'?'Source model request':'来源模型请求',{exact:true}).count(),0)
 })
 await tool.getByRole('button',{name:'View related request',exact:true}).click()
 const detail=page.locator('.request-trace-detail')
 await detail.getByText('Example model',{exact:true}).waitFor()
 await page.waitForFunction(()=>window.reads.length===2)
 assert.deepEqual(await page.evaluate(()=>window.reads),[{session:'owner',id:'related-request'},{session:'owner',id:'related-request'}],
  'The tool link must open its associated request, including its diagnostics read')
 const messages=detail.locator('.request-trace-disclosure').filter({has:page.locator('[data-message-role]')})
 await messages.locator('summary').click()
 const badges=messages.locator('[data-message-role]')
 await matrix(async(locale,dark,width)=>{
  assert.equal(await badges.count(),roles.length)
  assert.deepEqual(await badges.evaluateAll(nodes=>nodes.map(node=>node.getAttribute('data-message-role'))),roles)
  const styles=await badges.evaluateAll(nodes=>nodes.map(node=>{
   const css=getComputedStyle(node)
   return [css.backgroundColor,css.color,css.borderRadius,css.padding,css.fontSize,css.fontWeight]
  }))
  for(const style of styles)assert.deepEqual(style,styles[0],'All message roles must share badge appearance')
  assert.notEqual(styles[0][0],'rgba(0, 0, 0, 0)','Role badges must have a visible background')
  const labels=locale==='en'?['System instructions','Developer instructions','User message','Model responses and tool calls','Tool result','constructor']:['系统指令','开发者指令','用户消息','模型回复与工具调用','工具结果','constructor']
  assert.deepEqual(await badges.allTextContents(),labels)
  for(const role of roles)await messages.getByText('Original 原文 '+role,{exact:true}).waitFor()
  if(locale==='zh-CN'&&!dark&&width===1440&&process.env.HARNESS_TEST_TRACE_BADGES_SCREENSHOT)await messages.screenshot({path:process.env.HARNESS_TEST_TRACE_BADGES_SCREENSHOT})
 })
 assert.equal(await page.evaluate(()=>window.reads.length),2,'Language and theme changes must not refetch the request')
 await messages.locator('[data-message-role="assistant"]').click()
 await detail.locator('.request-trace-reader').getByText('Original 原文 assistant',{exact:true}).waitFor()
 assert.deepEqual(errors,[])
 await page.close()
 console.log('Trace message badges: shared role styles, original labels/content, related-request navigation, en→zh→en and theme/width matrix passed')
},{entry:'/__trace-badges-entry.js',vite:{plugins:[{
 name:'trace-badges-fixture',resolveId(id){if(id==='/__trace-badges-entry.js')return id},load(id){if(id==='/__trace-badges-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__trace-badges',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__trace-badges','<html><head><link rel="icon" href="data:,"></head><body><div id="root"></div><script type="module" src="/__trace-badges-entry.js"></script></body></html>'))}catch(error){next(error)}})},
}]}})

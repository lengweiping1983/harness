import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ContextStatisticsPanel} from '/src/components/inspector/ContextStatisticsPanel.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t
const specs=['partial','full','unknown','missingCoverage','invalid','tree','million','yi'].map(mode=>{
 const total=mode==='tree'?210000:mode==='million'?1000000:mode==='yi'?100000000:1200
 const usage={input_tokens:total-200,output_tokens:200,reasoning_tokens:50,cache_read_tokens:mode==='unknown'?0:100,cache_reported_input_tokens:mode==='unknown'?0:mode==='full'?1000:500,total_tokens:total}
 if(mode==='missingCoverage')delete usage.cache_reported_input_tokens
 return {mode,statistics:{usage,tree_cost:mode==='tree'?{usage:{...usage,total_tokens:410912},requests:14,reported_requests:14,unreported_requests:0,compaction_requests:0,cache_inconsistent_requests:0,session_count:5,body_bytes:1409475}:undefined,requests:2,reported_requests:1,unreported_requests:1,compaction_requests:0,cache_inconsistent_requests:mode==='invalid'?1:0,tool_calls:0,tool_errors:0,subagents:0}}
})
window.specs=specs;window.original=JSON.stringify(specs)
createRoot(document.getElementById('root')).render(React.createElement('main',null,...specs.map(({mode,statistics})=>React.createElement('div',{'data-statistics':mode,key:mode},React.createElement(ContextStatisticsPanel,{statistics})))))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[],requests=[]
 page.on('pageerror',error=>errors.push(error.message))
 await page.route('**/api/v1/**',route=>{requests.push(route.request().url());return route.fulfill({status:500,json:{error:'Unexpected request'}})})
 await page.goto(server.resolvedUrls.local[0]+'__context-statistics-language')
 const text=(key,params={})=>page.evaluate(({key,params})=>window.copy(key,params),{key,params})
 for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  assert.equal(await text('ui.context.compact'),locale==='zh-CN'?'整理历史':'Compact history')
  for(const mode of ['partial','full','unknown','missingCoverage','invalid','tree','million','yi']){
   const panel=page.locator('[data-statistics="'+mode+'"]')
   const disclosure=panel.locator('details')
   await disclosure.evaluate(node=>{node.open=false})
   await panel.getByText(await text('ui.context.statistics.currentMissing',{count:'1'}),{exact:true}).waitFor()
   if(mode==='tree'){
    await panel.getByText(locale==='zh-CN'?'本对话及所有子智能体的已报告总用量':'Reported usage for this conversation and all subagents',{exact:true}).waitFor()
    await panel.getByText(locale==='zh-CN'?'5 个对话 · 14 次请求，含所有子智能体。':'5 conversations · 14 requests, including all subagents.',{exact:true}).waitFor()
    await panel.getByText('410,912',{exact:true}).waitFor()
    await panel.getByText('410.9K',{exact:true}).waitFor()
   }else if(mode==='million'||mode==='yi'){
    await panel.getByText(mode==='million'?'1M':locale==='zh-CN'?'1亿':'100M',{exact:true}).waitFor()
   }
   if(mode==='invalid')await panel.getByText(await text('ui.context.statistics.invalidCache'),{exact:true}).waitFor()
   await disclosure.locator('summary').click()
   const cards=panel.locator('.grid-cols-2 > div')
   const keys=['ui.context.statistics.requests','ui.context.statistics.reportedRequests','ui.components.inspector.Inspector.a8ca3c13eb75','ui.components.inspector.Inspector.a6c975c0574a','ui.context.statistics.subagents']
   const labels=await Promise.all(keys.map(key=>text(key)))
   assert.deepEqual(await cards.locator(':scope > div:first-child').allTextContents(),labels,'Request counts must be adjacent, before tools and subagents')
   assert.deepEqual(await cards.locator(':scope > div:last-child').allTextContents(),['2','1','0','0','0'],'Reordering must preserve the values associated with each label')
   const boxes=await cards.evaluateAll(nodes=>nodes.map(node=>{const box=node.getBoundingClientRect();return {x:box.x,y:box.y}}))
   assert.equal(boxes[0].y,boxes[1].y,'Both request counts share the first row')
   assert.ok(boxes[1].x>boxes[0].x&&boxes[2].y>boxes[0].y,'Reported usage appears second; tools move to the next row')
   if(mode==='partial'&&locale==='zh-CN'&&!dark&&width===390&&process.env.HARNESS_TEST_STATISTICS_SCREENSHOT)await panel.locator('.grid-cols-2').screenshot({path:process.env.HARNESS_TEST_STATISTICS_SCREENSHOT})
   assert.equal(await panel.getByText(/明细只看当前对话|details cover only the selected conversation/i).count(),0)
   if(mode==='tree')await panel.getByText('210K',{exact:true}).waitFor()
   if(mode==='missingCoverage'){
    await panel.getByText('10.0%',{exact:true}).waitFor()
    assert.equal(await panel.getByText(await text('ui.context.statistics.invalidCache'),{exact:true}).count(),0)
   }else if(mode==='partial'){
    const expected=await text('ui.context.statistics.cacheCoverage',{reported:'500',input:'1,000'})
    await panel.getByText(expected,{exact:true}).waitFor()
    if(locale==='en')assert.equal(expected,"The provider has reported the request's cache-read ratio. Input covered by cache reports: 500 / 1,000 tokens.")
   }else if(mode==='full'||mode==='unknown')await panel.getByText(await text('ui.components.inspector.Inspector.'+(mode==='full'?'28582c90458d':'a4e23161f46c')),{exact:true}).waitFor()
  }
  assert.equal(await page.evaluate(()=>JSON.stringify(window.specs)===window.original),true)
 }
 assert.deepEqual(requests,[]);assert.deepEqual(errors,[]);await page.close()
 console.log('Statistics: bilingual usage units, subagent wording, cache states and warnings across languages, themes and widths')
},{entry:'/__context-statistics-language-entry.js',vite:{plugins:[{name:'statistics-language-fixture',resolveId(id){if(id==='/__context-statistics-language-entry.js')return id},load(id){if(id==='/__context-statistics-language-entry.js')return entry},configureServer(server){server.middlewares.use('/__context-statistics-language',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__context-statistics-language','<html><body><div id="root"></div><script type="module" src="/__context-statistics-language-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})

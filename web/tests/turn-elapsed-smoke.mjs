import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {Conversation} from '/src/components/chat/Conversation.tsx'
import {useAppStore} from '/src/store/app.ts'
import {emptySession,reduceEvent,displayBlocks} from '/src/store/chat.ts'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
const root=createRoot(document.getElementById('root'))
const start=Date.now()-80000
const user={kind:'user',id:'user',turn:1,sourceSeq:1,at:start,text:'Original request'}
const thought=(id,text,streaming=false)=>({kind:'thinking',id,turn:1,at:start+1,text,streaming})
const tool=(id,status='ok')=>({kind:'tool',id,callId:id,turn:1,at:start+2,name:'bash',status,input:{command:'echo Original'},output:'Original output',progress:status==='running'?{seq:1,stdout:'Live output 原文',stderr:'',stdoutEnd:1,stderrEnd:0,stdoutDropped:0,stderrDropped:0}:undefined})
const narrative=(id,text)=>({kind:'assistant',id,turn:1,at:start+3,text})
const apply=(state,event)=>({...state,...reduceEvent(state,event)})
let chainState=apply(emptySession(),{type:'turn/start',turn:1})
for(let index=0;index<12;index++){
 const text='Original thought '+index+' 原文'
 chainState=apply(chainState,{type:'assistant/delta',delta_type:'thinking',delta:text})
 chainState=apply(chainState,{type:'assistant/message',text:'',thinking:text})
}
window.appendThought=()=>{chainState=apply(chainState,{type:'assistant/delta',delta_type:'thinking',delta:'Live continuation'});window.render('chain','chain-owner')}
window.locale=setLocale
window.render=(mode='segments',session='timer-owner')=>{
 useAppStore.setState({activeSessionId:session})
 let blocks=[user],running=true,connection='open',activity={turn:1,startedAt:start,startSeq:0}
 if(mode!=='waiting')blocks.push(thought('a','First thought 原文'),tool('b'),narrative('note','Progress original'),thought('c','Second thought 原文'),tool('d'),thought('live','Live thought 原文',true),tool('active','running'))
 if(mode==='complete'||mode==='stopped'||mode==='interrupted'||mode==='ended'){
  activity={...activity,endedAt:start+177000,endSeq:20,reason:mode==='complete'?'completed':mode==='stopped'?'aborted':mode==='interrupted'?'interrupted':'error',finalResponseSeq:mode==='complete'?19:undefined};running=false
  blocks=blocks.map(block=>block.kind==='tool'?{...block,status:'ok'}:block.kind==='thinking'?{...block,streaming:false}:block)
  blocks.push({...narrative('final','Final original'),sourceSeq:19})
 }
 if(mode==='disconnected')connection='reconnecting'
 if(mode==='unknown')running=false
 if(mode==='missing')activity={turn:1,startSeq:0}
 if(mode==='settled')blocks=[user,thought('live','Live thought 原文',false)]
 if(mode==='single')blocks=[user,thought('live','Live thought 原文',true)]
 if(mode==='chain')blocks=[user,...displayBlocks(chainState)]
 root.render(React.createElement('div',{className:'conversation-pane',style:{height:1000,display:'flex'}},React.createElement(Conversation,{blocks,turns:{1:activity},running,streaming:running,connection,readOnly:true})))
}
window.render()
`

await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[]
 page.on('pageerror',error=>errors.push(error.message))
  await page.clock.install({time:new Date('2026-10-26T04:00:00Z')})
  await page.clock.pauseAt(new Date('2026-10-26T04:00:01Z'))
 await page.route('**/api/v1/sessions/*/turns/*/completion*',route=>route.fulfill({json:{turn:1,end_seq:20,final_response_seq:route.request().url().includes('complete-owner')?19:null,response_seq:19,ended_at:0,feedback:null,delivery:{run_id:'fixture',start_seq:0,tool_executions:0,failed_tools:0,unknown_tools:0,artifacts:[],artifact_count:0,verification:'unverified',checks:[]},hooks:{total:0,runs:0,blocked:0,unsuccessful:0,counts:{},duration_ms:0,rows:[],offset:0,next_offset:null}}}))
 await page.goto(server.resolvedUrls.local[0]+'__turn-elapsed')
 const timer=page.locator('[data-turn-elapsed="1"]')
 await timer.waitFor()
 for(const locale of ['en','zh-CN','en'])for(const [dark,width] of [[false,390],[true,1440]]){
  await page.setViewportSize({width,height:1200})
  await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark);window.render('segments')},{locale,dark})
  await page.waitForFunction(()=>document.querySelectorAll('[data-tool-run-turn="1"]').length===2)
  const bubble=await page.locator('[data-user-message-bubble]').boundingBox()
  const body=await page.locator('[data-row-id="note"]').boundingBox()
  assert.ok(bubble.x>body.x,'Own user bubble must align to the right of assistant content')
  await page.getByText('Progress original',{exact:true}).waitFor({state:'visible'})
  assert.equal(await page.getByText('Progress original',{exact:true}).isVisible(),true)
  const groups=page.locator('[data-tool-run-turn="1"] button')
  assert.equal(await groups.nth(0).getAttribute('aria-expanded'),'false')
  await groups.nth(0).click()
  const thought=page.locator('[data-row-id="a"] details')
  await thought.waitFor();assert.equal(await thought.evaluate(node=>node.open),false)
  await thought.locator('summary').focus();await page.keyboard.press('Enter')
  await page.waitForFunction(()=>document.querySelector('[data-row-id="a"] details')?.open)
  assert.equal(await groups.nth(1).getAttribute('aria-expanded'),'false')
  await groups.nth(0).click();await groups.nth(0).click()
  assert.equal(await thought.evaluate(node=>node.open),true)
  await thought.locator('summary').click();await groups.nth(0).click()
  const active=page.locator('[data-tool-call-id="active"]')
  assert.equal(await active.locator('pre').count(),0)
  await active.locator('button').first().click()
  assert.equal(await active.getByText('Live output 原文',{exact:true}).isVisible(),true)
  await active.locator('button').first().click();assert.equal(await active.locator('pre').count(),0)
  const before=await timer.textContent();await page.clock.runFor(2100)
  assert.notEqual(await timer.textContent(),before)
  const outer=page.locator('[data-row-id="activity-turn-1"] button')
  await outer.click()
  assert.equal(await active.isVisible(),true)
  const folded=await timer.textContent();await page.clock.runFor(2100)
  assert.notEqual(await timer.textContent(),folded)
  await outer.click()
 }
 await page.evaluate(()=>{window.locale('en');window.render('chain','chain-owner')})
 const chain=page.locator('[data-tool-run-turn="1"] button')
 await page.waitForFunction(()=>document.querySelector('[data-tool-run-turn="1"] button')?.textContent.includes('Thinking segments: 12'))
 assert.equal(await chain.count(),1)
 assert.equal(/characters|字符/i.test(await chain.innerText()),false)
 assert.equal(await page.locator('[data-row-id] details').count(),0)
 const chainId=await chain.locator('..').getAttribute('data-row-id')
 await chain.click()
 await page.waitForFunction(()=>document.querySelectorAll('[data-row-id] details').length>0)
 assert.equal(await page.locator('[data-row-id] details[open]').count(),0)
 await page.evaluate(()=>window.appendThought())
 await page.waitForFunction(()=>document.querySelector('[data-tool-run-turn="1"] button')?.textContent.includes('Thinking segments: 13'))
 assert.equal(await chain.getAttribute('aria-expanded'),'true')
 assert.equal(await chain.locator('..').getAttribute('data-row-id'),chainId)
 await chain.click()
 assert.equal(await page.locator('[data-row-id] details').count(),0)
 if(process.env.HARNESS_TIMING_SCREENSHOTS){
  await page.evaluate(()=>window.locale('zh-CN'))
  await page.screenshot({path:process.env.HARNESS_TIMING_SCREENSHOTS+'/harness-thinking-chain.png'})
  await page.evaluate(()=>window.locale('en'))
 }
 if(process.env.HARNESS_TIMING_SCREENSHOTS){
  await page.evaluate(()=>{window.locale('zh-CN');window.render('segments','preview-owner')})
  await page.waitForFunction(()=>document.querySelector('[data-turn-elapsed]')?.textContent.includes('已处理'))
  await page.screenshot({path:process.env.HARNESS_TIMING_SCREENSHOTS+'/harness-running.png'})
  await page.evaluate(()=>window.locale('en'))
 }
 await page.evaluate(()=>window.render('single','single-owner'))
 const live=page.locator('[data-row-id="live"] details')
 await live.waitFor();assert.equal(await live.evaluate(node=>node.open),false)
 await live.locator('summary').click();assert.equal(await live.evaluate(node=>node.open),true)
 await page.evaluate(()=>window.render('settled','single-owner'))
 await page.waitForFunction(()=>document.querySelector('[data-row-id="live"] details')?.open===false)
 await live.locator('summary').click()
 await page.evaluate(()=>window.render('settled','other-owner'))
 await page.waitForFunction(()=>document.querySelector('[data-row-id="live"] details')?.open===false)
 await page.evaluate(()=>window.render('settled','single-owner'))
 await page.waitForFunction(()=>document.querySelector('[data-row-id="live"] details')?.open===true)
 await page.evaluate(()=>{
  window.testHidden=true
  Object.defineProperty(document,'hidden',{configurable:true,get:()=>window.testHidden})
  document.dispatchEvent(new Event('visibilitychange'))
 })
 await page.waitForFunction(()=>document.hidden&&document.querySelector('[data-turn-elapsed]')?.dataset.timingActive==='false')
 const hiddenTime=await timer.textContent();await page.clock.runFor(5000)
 assert.equal(await timer.textContent(),hiddenTime)
 await page.evaluate(()=>{window.testHidden=false;document.dispatchEvent(new Event('visibilitychange'))})
 await page.waitForFunction(previous=>document.querySelector('[data-turn-elapsed]')?.textContent!==previous,hiddenTime)
 await page.evaluate(()=>window.render('complete','complete-owner'))
 await page.waitForFunction(()=>document.querySelector('[data-turn-elapsed]')?.textContent.includes('2 min 57 sec'))
 assert.equal(await timer.getAttribute('data-timing-active'),'false')
 await page.getByText('Final original',{exact:true}).waitFor({state:'visible'})
 assert.equal(await page.getByText('Final original',{exact:true}).isVisible(),true)
 const completed=await timer.textContent();await page.clock.runFor(5000)
 assert.equal(await timer.textContent(),completed)
 if(process.env.HARNESS_TIMING_SCREENSHOTS){
  await page.evaluate(()=>window.locale('zh-CN'))
  await page.waitForFunction(()=>document.querySelector('[data-turn-elapsed]')?.textContent.includes('用时'))
  await page.screenshot({path:process.env.HARNESS_TIMING_SCREENSHOTS+'/harness-completed.png'})
  await page.evaluate(()=>window.locale('en'))
 }
 for(const [mode,label] of [['stopped','Stopped after'],['interrupted','Interrupted after'],['ended','Ended after']]){
  await page.evaluate(mode=>window.render(mode,mode+'-owner'),mode)
  await page.waitForFunction(label=>document.querySelector('[data-turn-elapsed]')?.textContent.includes(label),label)
  assert.equal(await timer.getAttribute('data-timing-active'),'false')
  for(const locale of ['en','zh-CN','en']) {
   await page.evaluate(locale=>window.locale(locale),locale)
   const prefix=locale==='en'?label:mode==='stopped'?'已停止 · 用时':mode==='interrupted'?'已中断 · 用时':'已结束 · 用时'
   await page.waitForFunction(({prefix,locale})=>document.querySelector('[data-turn-elapsed]')?.textContent===prefix+' '+(locale==='en'?'2 min 57 sec':'2 分钟 57 秒'),{prefix,locale})
  }
 }
 for(const mode of ['disconnected','unknown']){
  await page.evaluate(mode=>window.render(mode,mode+'-owner'),mode)
  await page.waitForFunction(()=>document.querySelector('[data-turn-elapsed]')?.textContent.includes('awaiting synchronization'))
  assert.equal(await timer.getAttribute('data-timing-active'),'false')
 }
 await page.evaluate(()=>window.render('waiting','waiting-owner'))
 await timer.waitFor();assert.equal(await page.locator('[data-row-id="activity-turn-1"] button').isDisabled(),true)
 assert.equal(await timer.getAttribute('data-timing-active'),'true')
 await page.evaluate(()=>window.render('missing','missing-owner'))
 await page.waitForFunction(()=>document.querySelector('[data-turn-elapsed]')?.textContent.includes('elapsed time unavailable'))
 assert.deepEqual(errors,[])
 await page.close()
 console.log('Turn timing and activity segments: one stable group for 12→13 real reducer thoughts, no character counts, right-aligned user bubble, second ticks, terminal/unknown states, independent folds, streaming settlement and ownership; en→zh→en, themes and widths')
},{entry:'/__turn-elapsed-entry.js',vite:{plugins:[{
 name:'turn-elapsed-fixture',resolveId(id){if(id==='/__turn-elapsed-entry.js')return id},load(id){if(id==='/__turn-elapsed-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__turn-elapsed',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__turn-elapsed','<html><body><div id="root"></div><script type="module" src="/__turn-elapsed-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})

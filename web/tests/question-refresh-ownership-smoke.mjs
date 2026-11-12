import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {useSessionSocket} from '/src/hooks/useSessionSocket.ts'
import {useChatStore} from '/src/store/chat.ts'
import {useAppStore} from '/src/store/app.ts'
import {setLocale} from '/src/i18n/index.ts'
setLocale('en');useAppStore.setState({activeSessionId:'first',sessions:[]})
window.snapshot=id=>useChatStore.getState().get(id)
function Fixture(){const [id,setId]=React.useState('first');window.select=setId;useSessionSocket(id);return React.createElement('div',null,id)}
createRoot(document.getElementById('root')).render(React.createElement(Fixture))
`
await browserScenario(async({server,browser})=>{
 for(const mode of ['slow-poll','queued-event','switch']){
  const page=await browser.newPage();page.setDefaultTimeout(15000)
  await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
  let notifyRead=()=>{}
  const reads=[],errors=[],wires=new Map();
  const readAt=count=>reads.length>=count?Promise.resolve():new Promise(resolve=>{notifyRead=()=>{if(reads.length>=count)resolve()}}); page.on('pageerror',error=>errors.push(error.message))
  await page.routeWebSocket('**/ws?*',socket=>{
   const id=new URL(socket.url()).searchParams.get('session');wires.set(id,socket)
   socket.send(JSON.stringify({type:'ready',server_id:'fixture-server',last_seq:0,running:true,snapshot:{header:{id},events:[],messages:[],usage:{},stats:{},todos:[],timeline:[]}}))
  })
  await page.route('**/api/v1/**',async route=>{
   const url=new URL(route.request().url())
   if(url.pathname==='/api/v1/questions'){
    let release;const gate=new Promise(resolve=>{release=resolve})
    const record={id:url.searchParams.get('session_id'),release,done:false};reads.push(record);notifyRead()
    const questions=await gate;await route.fulfill({json:{questions}});record.done=true;return
   }
   return route.fulfill({status:503,json:{error:'fixture offline',code:'fixture/offline'}})
  })
  await page.goto(server.resolvedUrls.local[0]+'__question-refresh')
  await page.waitForFunction(()=>window.snapshot('first').serverId==='fixture-server')
  await readAt(1);assert.equal(reads.length,1)
  if(mode==='slow-poll'){
   await page.clock.runFor(12000);assert.equal(reads.length,1,'Slow question reads must not overlap or be invalidated by timer ticks')
   reads[0].release([{question_id:'fresh',questions:[]}])
   await page.waitForFunction(()=>window.snapshot('first').questions[0]?.question_id==='fresh')
   await page.evaluate(()=>Object.defineProperty(document,'hidden',{configurable:true,get:()=>true}))
   await page.clock.runFor(4000);assert.equal(reads.length,1)
   await page.evaluate(()=>Object.defineProperty(document,'hidden',{configurable:true,get:()=>false}))
   await page.clock.runFor(4000);await readAt(2)
   reads[1].release([{question_id:'fresh',questions:[{id:'q',question:'Updated 原文',options:[]}]}])
   await page.waitForFunction(()=>window.snapshot('first').questions[0]?.questions[0]?.question==='Updated 原文')
  }else if(mode==='queued-event'){
   const wire=wires.get('first')
   for(const seq of [1,2])wire.send(JSON.stringify({type:'user_question/opened',seq,server_id:'fixture-server'}))
   await page.waitForFunction(()=>window.snapshot('first').lastSeq===2)
   assert.equal(reads.length,1)
   const response=page.waitForResponse(response=>response.url().includes('/api/v1/questions'))
   reads[0].release([{question_id:'stale',questions:[]}]);await response;await readAt(2)
   assert.equal(reads.length,2);assert.deepEqual(await page.evaluate(()=>window.snapshot('first').questions),[])
   reads[1].release([{question_id:'latest',questions:[]}])
   await page.waitForFunction(()=>window.snapshot('first').questions[0]?.question_id==='latest')
   assert.equal(reads.length,2)
  }else{
   const aborted=page.waitForEvent('requestfailed',{predicate:request=>request.url().includes('session_id=first')})
   const next=page.waitForRequest(request=>request.url().includes('session_id=second'))
   await page.evaluate(()=>window.select('second'));await next;await readAt(2)
   await page.waitForFunction(()=>window.snapshot('second').serverId==='fixture-server')
   assert.equal(reads.length,2)
   const failed=await aborted
   assert.ok(failed.failure(),'Old question observation must actually abort')
   reads[0].release([{question_id:'old',questions:[]}])
   reads[1].release([{question_id:'new',questions:[]}])
   await page.waitForFunction(()=>window.snapshot('second').questions[0]?.question_id==='new')
   assert.deepEqual(await page.evaluate(()=>window.snapshot('first').questions),[])
  }
  assert.deepEqual(errors,[]);await page.close();console.log(mode+': serial question refresh preserves ownership')
 }
},{entry:'/__question-refresh-entry.js',vite:{plugins:[{
 name:'question-refresh-fixture',resolveId(id){if(id==='/__question-refresh-entry.js')return id},load(id){if(id==='/__question-refresh-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__question-refresh',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__question-refresh','<html><body><div id="root"></div><script type="module" src="/__question-refresh-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})

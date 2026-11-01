import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const goal={stateVersion:1,activation:'armed',goal:{id:'original-goal',revision:1,objective:'Original objective 原文',criteria:['Original objective 原文'],phase:'active',roundsStarted:1,maxGoalRounds:null}}
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {useSessionActivity} from '/src/store/sessionActivity.ts'
import {useSessionSocket} from '/src/hooks/useSessionSocket.ts'
import {Composer} from '/src/components/chat/Composer.tsx'
import {Toaster} from '/src/components/overlays/CommandPalette.tsx'
import {useAppStore} from '/src/store/app.ts'
import {useDraftStore} from '/src/store/drafts.ts'
import {useChatStore} from '/src/store/chat.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t
window.store=useAppStore
useAppStore.setState({activeSessionId:'fixture',sessions:[{id:'fixture',cwd:'/fixture'}],toasts:[]})
useDraftStore.getState().setText('fixture','User draft 原文')
window.snapshot=()=>({toasts:useAppStore.getState().toasts,submissions:useDraftStore.getState().submissions.fixture??[],serverId:useChatStore.getState().sessions.fixture?.serverId})
function Fixture(){useSessionActivity(false);const channel=useSessionSocket('fixture');window.send=channel.send;window.connection=channel.connection;return React.createElement(React.Fragment,null,React.createElement(Composer,{running:false,queued:0,planMode:false,connection:channel.connection,onSend:channel.send,onCancel:()=>{},onTogglePlan:()=>{}}),React.createElement(Toaster))}
createRoot(document.getElementById('root')).render(React.createElement(Fixture))
`
const keys={
 'auto-success':['ui.notifications.completed','ui.components.sidebar.ProjectSidebar.a91ae6035092'],
 'auto-failed':['ui.notifications.failed','ui.components.sidebar.ProjectSidebar.a91ae6035092'],
 'not-ready':['ui.hooks.useSessionSocket.dfba1899c6d4','ui.hooks.useSessionSocket.693dab70d76d'],
 'send-unavailable':['ui.hooks.useSessionSocket.ac77953a1e06','ui.hooks.useSessionSocket.e1e257086621'],
 'goal-error':['ui.components.chat.GoalDock.825cfa17d9cd','ui.http.invalidResponse'],
 'reasoning-pending':['ui.models.reasoningBeforeSend','ui.models.reasoningBeforeSend'],
 'reasoning-unknown':['ui.models.reasoningBeforeSend','ui.models.reasoningBeforeSend'],
}
const selected=process.argv.slice(2)
if(selected.some(view=>!Object.hasOwn(keys,view)))throw Error('Unknown socket notification scenario')
await browserScenario(async({server,browser})=>{
 for(const [view,[titleKey,detailKey]] of Object.entries(keys).filter(([view])=>!selected.length||selected.includes(view))){
  const page=await browser.newPage();page.setDefaultTimeout(15000)
  await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
  const sent=[],requests=[],errors=[];let wire,acceptSend;page.on('pageerror',error=>errors.push(error.message))
  await page.routeWebSocket('**/ws?*',socket=>{
   wire=socket;socket.onMessage(message=>{const frame=JSON.parse(String(message));sent.push(frame);if(frame.action==='send')acceptSend?.()})
   if(view==='not-ready')return
   socket.send(JSON.stringify({type:'ready',server_id:'fixture-server',last_seq:0,running:view==='goal-error'}))
   if(view.startsWith('auto')){
    socket.send(JSON.stringify({type:'run/state',server_id:'fixture-server',seq:1,id:'actual-auto-run',started_at:1,status:view==='auto-success'?'completed':'failed'}))
    socket.send(JSON.stringify({type:'automation/completed',server_id:'fixture-server',seq:2,run_id:'automation-history-run',status:view==='auto-success'?'completed':'failed'}))
   }
  })
  await page.route('**/api/v1/**',route=>{
   const request=route.request(),path=new URL(request.url()).pathname;requests.push({method:request.method(),path})
   if(path.endsWith('/goal'))return request.method()==='POST'?route.fulfill({status:200,body:'broken',contentType:'application/json'}):route.fulfill({json:goal})
   if(path==='/api/v1/sessions/activity')return route.fulfill({json:{running:[],catalog_revision:0,server_id:'fixture-server',completion_cursor:0,completions:[],completions_lost:false}})
   if(path==='/api/v1/sessions')return route.fulfill({json:{sessions:[{id:'fixture',cwd:'/fixture'}]}})
   return route.fulfill({status:503,json:{code:'fixture/offline',error:'Original diagnostic'}})
  })
  await page.goto(server.resolvedUrls.local[0]+'__socket-notification-language')
  if(view==='not-ready'){
   await page.waitForFunction(()=>window.connection==='open')
   assert.equal(await page.evaluate(()=>window.send('Original task 原文')),false)
  }else{
   await page.waitForFunction(()=>window.snapshot().serverId==='fixture-server')
   if(view==='send-unavailable'){
    wire.close();await page.waitForFunction(()=>window.connection==='reconnecting')
    const listed=page.waitForResponse(response=>response.url().endsWith('/api/v1/sessions'))
    assert.equal(await page.evaluate(()=>window.send('Original task 原文')),false);await listed
   }
   if(view==='goal-error')assert.equal(await page.evaluate(()=>window.send('/goal pause')),false)
   if(view.startsWith('reasoning-')){
    await page.evaluate(view=>window.store.setState({defaultReasoningEffort:'high',defaultReasoningSave:{pending:view==='reasoning-pending',error:null,unconfirmed:view==='reasoning-unknown'}}),view)
    assert.equal(await page.evaluate(()=>window.send('Original task 原文')),false)
   }
  }
  const wait=async()=>{
   const expected=await page.evaluate(({titleKey,detailKey})=>[window.copy(titleKey),window.copy(detailKey)],{titleKey,detailKey})
   await page.waitForFunction(expected=>expected.every(text=>document.body.textContent.includes(text)),expected)
  }
  await wait();if(view.startsWith('auto'))assert.equal((await page.evaluate(()=>window.snapshot().toasts)).length,1);const reads=requests.length,frames=sent.length
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
   await wait();assert.equal(requests.length,reads);assert.equal(sent.length,frames)
   assert.equal(await page.locator('textarea').inputValue(),'User draft 原文')
   assert.deepEqual(await page.evaluate(()=>window.snapshot().submissions),[])
  }
  assert.equal(sent.filter(frame=>frame.action==='send').length,0)
  assert.equal(requests.filter(request=>request.method==='POST').length,view==='goal-error'?1:0)
  if(view==='goal-error')assert.equal(requests.filter(request=>request.path.endsWith('/goal')&&request.method==='GET').length,2)
  if(view.startsWith('reasoning-')){
   let timeout
   const accepted=new Promise((resolve,reject)=>{acceptSend=resolve;timeout=setTimeout(()=>reject(Error('Explicit override send was not delivered')),15000)})
   await page.evaluate(()=>window.store.setState({sessions:[{id:'fixture',cwd:'/fixture',reasoning_effort:'low',metadata:{reasoning_effort:'low'}}]}))
   assert.equal(await page.evaluate(()=>window.send('Explicit override 原文')),true)
   try{await accepted}finally{clearTimeout(timeout)}
   assert.equal(sent.filter(frame=>frame.action==='send').length,1)
  }
  assert.deepEqual(errors,[]);await page.close();console.log(view+': notification follows locale, draft preserved, no send or write replay')
 }
},{entry:'/__socket-notification-language-entry.js',vite:{plugins:[{
 name:'socket-notification-language-fixture',resolveId(id){if(id==='/__socket-notification-language-entry.js')return id},load(id){if(id==='/__socket-notification-language-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__socket-notification-language',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__socket-notification-language','<html><body><div id="root"></div><script type="module" src="/__socket-notification-language-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})

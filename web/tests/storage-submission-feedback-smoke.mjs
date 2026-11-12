import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React,{useRef} from 'react'
import {createRoot} from 'react-dom/client'
import {Composer} from '/src/components/chat/Composer.tsx'
import {ComposerAttachments} from '/src/components/chat/ComposerAttachments.tsx'
import {useAttachmentDrafts} from '/src/hooks/useAttachmentDrafts.ts'
import {useSessionSocket} from '/src/hooks/useSessionSocket.ts'
import {useAppStore} from '/src/store/app.ts'
import {useDraftStore} from '/src/store/drafts.ts'
import {useChatStore} from '/src/store/chat.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t
const view=new URLSearchParams(location.search).get('view')
useAppStore.setState({activeSessionId:'fixture',sessions:[{id:'fixture',cwd:'/fixture'}]})
if(view!=='upload')useDraftStore.getState().rememberSubmission('fixture',{id:'original',text:'User request 原文',attachments:[],action:'send',status:'sending',sendOnReady:true})
window.snapshot=()=>({entries:useDraftStore.getState().submissions.fixture,serverId:useChatStore.getState().sessions.fixture?.serverId})
function Upload(){const attachments=useAttachmentDrafts(),pickerRef=useRef(null);window.addFiles=attachments.addFiles;return React.createElement(ComposerAttachments,{pickerRef,attachments})}
function Fixture(){const channel=useSessionSocket(view==='upload'?null:'fixture');return view==='upload'?React.createElement(Upload):React.createElement(Composer,{running:false,queued:0,planMode:false,connection:channel.connection,onSend:()=>{throw Error('Unexpected submit')},onCancel:()=>{},onTogglePlan:()=>{}})}
createRoot(document.getElementById('root')).render(React.createElement(Fixture))
`
const cases=[['upload','attachment/invalid-storage'],['ws-storage','attachment/invalid-storage'],['ws-refs','attachment/invalid-refs'],['ws-unavailable','attachment/unavailable'],['ws-materialize','attachment/materialize-failed'],['ws-empty','fixture/empty']]
await browserScenario(async({server,browser})=>{
 for(const [view,code] of cases){
  const page=await browser.newPage();page.setDefaultTimeout(15000)
  await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
  const errors=[],sent=[],requests=[];page.on('pageerror',error=>errors.push(error.message))
  await page.routeWebSocket('**/ws?*',socket=>{
   socket.onMessage(message=>{const frame=JSON.parse(String(message));sent.push(frame);if(frame.action==='resume')socket.send(JSON.stringify({type:'ready',server_id:'fixture-server',last_seq:0,running:false}))})
   socket.send(JSON.stringify({type:'error',code,request_id:'original',submitted:false,message:view==='ws-empty'?'':'Host snapshot 原文'}))
  })
  await page.route('**/api/v1/**',route=>{
   requests.push({method:route.request().method(),url:route.request().url()})
   return route.fulfill({status:503,json:{code:route.request().url().endsWith('/attachments')?code:'fixture/offline',error:'Host snapshot 原文'}})
  })
  await page.goto(server.resolvedUrls.local[0]+'__storage-submission-feedback?view='+view)
  if(view==='upload'){
   await page.waitForFunction(()=>typeof window.addFiles==='function')
   await page.evaluate(()=>window.addFiles([new File(['Original'],'User 原文.txt')]))
  }else await page.waitForFunction(()=>window.snapshot().serverId==='fixture-server')
  const expected=()=>page.evaluate(({view,code})=>window.copy(view==='ws-empty'?'ui.hooks.useSessionSocket.9f22a898502c':'ui.collaboration.error.'+code,{code}),{view,code})
  const wait=async()=>page.waitForFunction(({view,expected})=>view==='upload'?document.querySelector('[role=listitem]')?.title===expected:document.body.textContent.includes(expected),{view,expected:await expected()})
  await wait();const before=requests.length,frames=sent.length
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
   await wait();assert.equal(requests.length,before);assert.equal(sent.length,frames)
   if(view!=='upload'){
    const state=await page.evaluate(()=>window.snapshot())
    assert.equal(state.entries.length,1);assert.equal(state.entries[0].status,'rejected');assert.equal(state.entries[0].text,'User request 原文')
   }
  }
  assert.equal(sent.filter(frame=>frame.action==='send').length,0,'ready must not replay a rejected request')
  assert.equal(requests.filter(request=>request.method==='POST').length,view==='upload'?1:0)
  assert.deepEqual(errors,[]);await page.close();console.log(view+': resource feedback follows locale, rejected request retained and never replayed on ready')
 }
},{entry:'/__storage-submission-feedback-entry.js',vite:{plugins:[{
 name:'storage-submission-feedback-fixture',resolveId(id){if(id==='/__storage-submission-feedback-entry.js')return id},load(id){if(id==='/__storage-submission-feedback-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__storage-submission-feedback',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__storage-submission-feedback','<html><body><div id="root"></div><script type="module" src="/__storage-submission-feedback-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})

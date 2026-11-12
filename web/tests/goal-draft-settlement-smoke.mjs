import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const original={objective:'Original goal 原文',rounds:'',budget:''}
const replacement={objective:'Replacement goal 新草稿',rounds:'7',budget:'9000'}
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {GoalDock} from '/src/components/chat/GoalDock.tsx'
import {useDraftStore} from '/src/store/drafts.ts'
import {useChatStore} from '/src/store/chat.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.copy=t
useDraftStore.getState().setExecutionMode('fixture','goal')
useDraftStore.getState().setGoalCreation('fixture',${JSON.stringify(original)})
window.snapshot=()=>({draft:useDraftStore.getState().bySession.fixture?.goalCreation??null,mode:useDraftStore.getState().bySession.fixture?.executionMode??null,goal:useChatStore.getState().get('fixture').goalState.goal})
window.replace=()=>useDraftStore.getState().setGoalCreation('fixture',${JSON.stringify(replacement)})
function Fixture(){const [shown,setShown]=React.useState(true);window.hide=()=>setShown(false);return shown?React.createElement(GoalDock,{sessionId:'fixture',state:{stateVersion:0,activation:'disarmed',goal:null},running:false,openRequest:1}):React.createElement('div',null,'Closed fixture')}
createRoot(document.getElementById('root')).render(React.createElement(Fixture))
`
await browserScenario(async({server,browser})=>{
 for(const mode of ['unmounted-replaced','mounted-replaced','unmounted-original','mounted-original']){
  const page=await browser.newPage();page.setDefaultTimeout(15000)
  const errors=[],writes=[],unexpected=[];let release,entered
  const gate=new Promise(resolve=>{release=resolve}),started=new Promise(resolve=>{entered=resolve})
  page.on('pageerror',error=>errors.push(error.message))
  await page.route('**/api/v1/**',async route=>{
   const request=route.request()
   if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/v1/sessions/fixture/goal'){
    writes.push(request.postDataJSON());entered();await gate
    return route.fulfill({json:{stateVersion:1,activation:'armed',goal:{id:'accepted',revision:1,objective:original.objective,criteria:[original.objective],phase:'active',roundsStarted:0,maxGoalRounds:null,tokenBudget:null}}})
   }
   unexpected.push(request.url());return route.fulfill({status:500,json:{error:'Unexpected request'}})
  })
  await page.goto(server.resolvedUrls.local[0]+'__goal-draft-settlement')
  const submit=page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.components.chat.GoalDock.781565e70304')),exact:true})
  await submit.click();await started
  if(mode.startsWith('unmounted')){await page.evaluate(()=>window.hide());await page.getByText('Closed fixture',{exact:true}).waitFor()}
  if(mode.endsWith('replaced'))await page.evaluate(()=>window.replace())
  const response=page.waitForResponse(response=>response.request().method()==='POST')
  release();await response
  await page.waitForFunction(()=>window.snapshot().goal?.id==='accepted')
  // 读取响应后再经过页面任务边界，确保控制函数的草稿收尾已经执行。
  await page.evaluate(()=>new Promise(resolve=>setTimeout(resolve,0)))
  const snapshot=await page.evaluate(()=>window.snapshot())
  assert.deepEqual(snapshot.draft,mode.endsWith('replaced')?replacement:null,mode)
  assert.equal(snapshot.mode,mode.endsWith('replaced')?'goal':null,mode)
  assert.equal(writes.length,1);assert.equal(writes[0].objective,original.objective)
  assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[])
  await page.close();console.log(mode+': accepted goal settles only its original draft')
 }
},{entry:'/__goal-draft-settlement-entry.js',vite:{plugins:[{
 name:'goal-draft-settlement-fixture',resolveId(id){if(id==='/__goal-draft-settlement-entry.js')return id},load(id){if(id==='/__goal-draft-settlement-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__goal-draft-settlement',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__goal-draft-settlement','<html><body><div id="root"></div><script type="module" src="/__goal-draft-settlement-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})

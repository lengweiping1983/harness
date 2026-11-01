import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const plan={id:'plan',title:'Fixture plan',status:'approved',feedback:'',previous_id:null,seq:1,submitted_at:1,decided_at:2,plan:'# First readable plan',diff:'',implementation:[]}
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {PlanHistory} from '/src/components/inspector/PlanHistory.tsx'
import {Inspector} from '/src/components/inspector/Inspector.tsx'
import {WorktreesPanel} from '/src/components/overlays/WorktreesPanel.tsx'
import {ProjectHooksPage} from '/src/components/overlays/ProjectHooksPage.tsx'
import {BrowserActivityBar} from '/src/components/inspector/BrowserActivityBar.tsx'
import {useAppStore} from '/src/store/app.ts'
import {useChatStore} from '/src/store/chat.ts'
import {api} from '/src/lib/api.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
const root=createRoot(document.getElementById('root'));window.reads=[];window.copy=t;window.plan=${JSON.stringify(plan)}
const pending=(path,signal)=>new Promise(resolve=>window.reads.push({path,signal,resolve}))
window.fetch=(path,options={})=>{
 if(String(path).includes('/plugin-packages'))return Promise.resolve(new Response(JSON.stringify({error:'Controlled plugin catalog failure'}),{status:503}))
 return pending(String(path),options.signal)
}
api.workspace=(_p,_r,options)=>pending('workspace',options?.signal)
useAppStore.setState({activeSessionId:'fixture',sessions:[{id:'fixture',title:'Fixture',cwd:'/fixture'}],inspectorTab:'files',workspace:{root:'/fixture'},workspaces:[{id:'/fixture',path:'/fixture',name:'Fixture'}],settingsWorkspaceRoot:'/fixture'})
useChatStore.getState().markLoading('fixture',false)
window.hidden=false;Object.defineProperty(document,'hidden',{get:()=>window.hidden,configurable:true})
window.visibility=value=>{window.hidden=value;document.dispatchEvent(new Event('visibilitychange'))}
window.show=(kind,active,language)=>{setLocale(language);root.render(kind==='plan'?React.createElement(PlanHistory,{sessionId:'fixture',active}):kind==='git'?React.createElement(WorktreesPanel,{active}):kind==='hooks'?React.createElement(ProjectHooksPage):kind==='browser'?React.createElement(BrowserActivityBar,{sessionId:'fixture',pageId:'page',active}):React.createElement(Inspector,{visible:active}))}
window.revise=()=>useChatStore.setState(state=>({sessions:{...state.sessions,fixture:{...state.sessions.fixture,planRevision:(state.sessions.fixture?.planRevision??0)+1}}}))
window.finish=(index,body,status=200)=>{window.reads[index].settled=true;window.reads[index].resolve(new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}}))}
`
await browserScenario(async({server,browser})=>{
 for(const kind of ['plan','git','hooks','browser','files'])for(const language of ['en','zh-CN']){
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message))
  await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
  await page.goto(server.resolvedUrls.local[0]+'__tab-reads');await page.waitForFunction(()=>typeof window.show==='function')
  await page.evaluate(args=>window.show(...args),[kind,true,language])
  if(kind==='files')await page.clock.runFor(32)
  await page.waitForFunction(()=>window.reads.length>0)
  if(kind==='plan'){
   for(let i=0;i<5;i++){await page.evaluate(()=>window.revise());await page.clock.runFor(32)}
   assert.equal(await page.evaluate(()=>window.reads.length),1)
   assert.equal(await page.evaluate(()=>window.reads[0].signal.aborted),false)
   await page.evaluate(()=>window.finish(0,{plans:[window.plan]}))
   await page.waitForFunction(()=>window.reads.some(r=>r.path.endsWith('/plans/plan')))
   const detail=await page.evaluate(()=>window.reads.findIndex(r=>r.path.endsWith('/plans/plan')))
   for(let i=0;i<5;i++){await page.evaluate(()=>window.revise());await page.clock.runFor(32)}
   assert.equal(await page.evaluate(()=>window.reads.filter(r=>r.path.endsWith('/plans/plan')).length),1)
   assert.equal(await page.evaluate(index=>window.reads[index].signal.aborted,detail),false)
   await page.evaluate(index=>window.finish(index,{plan:window.plan}),detail)
   await page.getByRole('heading',{name:'First readable plan',exact:true}).waitFor()
  }
  if(kind==='hooks'){
   await page.clock.runFor(9000)
   assert.equal(await page.evaluate(()=>window.reads.filter(r=>r.path.includes('/hooks')).length),1,'Slow Hook reads must not overlap')
  }
  if(kind==='hooks'||kind==='browser')await page.evaluate(()=>window.visibility(true))
  else await page.evaluate(args=>window.show(...args),[kind,false,language])
  await page.waitForFunction(()=>window.reads.every(r=>r.settled||r.signal?.aborted))
  const count=await page.evaluate(()=>window.reads.length)
  await page.clock.runFor(10000);assert.equal(await page.evaluate(()=>window.reads.length),count)
  await page.evaluate(kind=>window.reads.forEach((r,index)=>kind==='files'?r.resolve({ok:false,message:'STALE READ'}):window.finish(index,{error:'STALE READ'},503)),kind)
  await page.clock.runFor(32);assert.equal(await page.getByText('STALE READ',{exact:true}).count(),0)
  if(kind==='hooks'||kind==='browser')await page.evaluate(()=>window.visibility(false))
  else await page.evaluate(args=>window.show(...args),[kind,true,language])
  if(kind==='files')await page.clock.runFor(32)
  await page.waitForFunction(count=>window.reads.length>count,count)
  assert.deepEqual(errors,[]);await page.close();console.log(kind+' '+language+': no starving/overlapping reads, hidden cancellation, stale isolation and resume passed')
 }
},{entry:'/__tab-reads-entry.js',vite:{plugins:[{
 name:'tab-reads-fixture',resolveId(id){if(id==='/__tab-reads-entry.js')return id},load(id){if(id==='/__tab-reads-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__tab-reads',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__tab-reads','<html><body><div id="root"></div><script type="module" src="/__tab-reads-entry.js"></script></body></html>'))}catch(e){next(e)}})}
}]}})

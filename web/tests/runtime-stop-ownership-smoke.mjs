import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {flushSync} from 'react-dom'
import {RuntimeTab} from '/src/components/inspector/RuntimeTab.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;window.calls=[];window.stopResolvers=[]
useAppStore.setState({activeSessionId:'fixture'})
const params=new URLSearchParams(location.search),kind=params.get('kind'),outcome=params.get('outcome')
const terminal={sessionId:'terminal_fixture',type:'fixture',cwd:'/fixture',pid:123,status:{kind:'running'},name:'Original terminal 原文'}
const agent={agent_id:'agent_fixture',description:'Original Agent 原文',status:'running',parent_session_id:'fixture',created_at:0,duration_ms:0,tool_calls:0,pending_messages:0}
const job={id:'job_fixture',kind:'shell',label:'Original job 原文',status:'running',startedAt:0}
const snapshot={terminals:kind==='terminal'?[terminal]:[],subagents:kind==='agent'?[agent]:[],jobs:kind==='job'?[job]:[]}
const response=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}})
let reads=0
window.fetch=async(path,options={})=>{
 const method=options.method||'GET';window.calls.push({path,method})
 if(path==='/api/v1/runtime?session_id=fixture'){
  if(++reads===1)return response(snapshot)
  return new Promise(resolve=>{window.releasePoll=()=>resolve(response({code:'fixture/old',error:'Obsolete poll failure 原文'},503))})
 }
 if(method==='POST'&&path.endsWith('/stop'))return new Promise(resolve=>window.stopResolvers.push(()=>resolve(outcome==='reject'?response({code:'fixture/rejected',error:'Original rejection 原文'},400):response(kind==='terminal'?{terminal:{...terminal,status:{kind:'exited',exit_code:0}}}:kind==='agent'?{agent:{...agent,status:'interrupted',prompt:'Original prompt 原文'}}:{job:{...job,status:'killed'},outcome:'killed'}))))
 throw Error('Unexpected runtime request '+method+' '+path)
}
window.burst=node=>flushSync(()=>{node.dispatchEvent(new MouseEvent('click',{bubbles:true}));node.dispatchEvent(new MouseEvent('click',{bubbles:true}))})
window.releaseStop=()=>window.stopResolvers.forEach(resolve=>resolve())
createRoot(document.getElementById('root')).render(React.createElement(RuntimeTab))
`
const stopKeys={terminal:'ui.components.inspector.RuntimeTab.2993cb23dc3e',agent:'ui.components.inspector.RuntimeTab.11820a8941c3',job:'ui.components.inspector.RuntimeTab.a33b3101a358'}
await browserScenario(async({server,browser})=>{
 for(const kind of ['terminal','agent','job'])for(const outcome of ['success','reject']){
  const page=await browser.newPage();page.setDefaultTimeout(15000);await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
  const errors=[];page.on('pageerror',error=>errors.push(error.message))
  await page.goto(server.resolvedUrls.local[0]+'__runtime-stop?kind='+kind+'&outcome='+outcome)
  const stop=page.getByRole('button',{name:await page.evaluate(key=>window.copy(key),stopKeys[kind]),exact:true})
  await stop.waitFor()
  if(outcome==='success'){await page.clock.runFor(2000);await page.waitForFunction(()=>typeof window.releasePoll==='function')}
  await stop.evaluate(node=>window.burst(node))
  await page.waitForFunction(()=>window.stopResolvers.length>0)
  assert.equal(await page.evaluate(()=>window.calls.filter(call=>call.method==='POST').length),1,'One synchronous click burst must submit one stop')
  await page.evaluate(()=>window.releaseStop())
  if(outcome==='reject')await page.getByText('Original rejection 原文',{exact:true}).waitFor()
  else{
   await stop.waitFor({state:'detached'})
   await page.evaluate(()=>window.releasePoll());await page.clock.runFor(32)
   assert.equal(await page.getByText(/Obsolete poll failure/).count(),0,'Old poll failure must not override confirmed stop')
  }
  const calls=await page.evaluate(()=>window.calls.length)
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
   assert.equal(await page.evaluate(()=>window.calls.length),calls)
   if(outcome==='reject')await page.getByText('Original rejection 原文',{exact:true}).waitFor()
  }
  if(outcome==='reject'){
   await stop.click();await page.waitForFunction(()=>window.stopResolvers.length===2)
   await page.evaluate(()=>window.releaseStop())
   await stop.evaluate(node=>new Promise((resolve,reject)=>{
    if(!node.disabled){resolve();return}
    const timer=setTimeout(()=>{observer.disconnect();reject(Error('Stop button did not recover after refusal'))},5000)
    const observer=new MutationObserver(()=>{if(!node.disabled){clearTimeout(timer);observer.disconnect();resolve()}})
    observer.observe(node,{attributes:true,attributeFilter:['disabled']})
   }))
   assert.equal(await stop.isEnabled(),true,'An explicit rejection must release the synchronous stop lock')
   assert.equal(await page.evaluate(()=>window.calls.filter(call=>call.method==='POST').length),2)
  }
  assert.deepEqual(errors,[]);await page.close();console.log(kind+' '+outcome+': one accepted stop, current result and original diagnostics retained')
 }
},{entry:'/__runtime-stop-entry.js',vite:{plugins:[{
 name:'runtime-stop-fixture',resolveId(id){if(id==='/__runtime-stop-entry.js')return id},load(id){if(id==='/__runtime-stop-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__runtime-stop',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__runtime-stop','<html><body><div id="root"></div><script type="module" src="/__runtime-stop-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})

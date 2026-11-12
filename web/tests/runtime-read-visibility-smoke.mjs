import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {RuntimeTab} from '/src/components/inspector/RuntimeTab.tsx'
import {api} from '/src/lib/api.ts'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
let root=createRoot(document.getElementById('root'));window.reopen=()=>{root.unmount();root=createRoot(document.getElementById('root'))};window.reads=[];window.copy=t;window.hold=false
const pending=(kind,signal)=>new Promise(resolve=>window.reads.push({kind,signal,resolve}))
const terminal={sessionId:'terminal',type:'fixture',cwd:'/fixture',pid:123,status:{kind:'running'},name:'Fixture terminal'}
const agent={agent_id:'agent',description:'Fixture member',status:'running',parent_session_id:'fixture',created_at:0,duration_ms:0,tool_calls:0,pending_messages:0}
const job={id:'job',kind:'shell',label:'Fixture job',status:'running',startedAt:0}
api.runtime=(_s,signal)=>window.hold?pending('runtime',signal):Promise.resolve({ok:true,data:{terminals:[terminal],subagents:[agent],jobs:[job],subagents_has_more:true,subagents_next_before:'older',jobs_has_more:true,jobs_next_before:'older'}})
api.terminalTail=(_s,_i,signal)=>pending('terminal',signal)
api.subagentDetail=(_s,_i,signal)=>pending('agent',signal)
api.jobOutput=(_s,_i,signal)=>pending('job',signal)
api.subagentHistory=(_s,_i,signal)=>pending('agentHistory',signal)
api.jobHistory=(_s,_i,signal)=>pending('jobHistory',signal)
useAppStore.setState({activeSessionId:'fixture'})
window.show=(active,focus,language)=>{setLocale(language);useAppStore.setState({inspectorJobTarget:focus?{sessionId:'fixture',jobId:'focus',seq:1}:null});root.render(React.createElement(RuntimeTab,{active}))}
`
await browserScenario(async({server,browser})=>{
 for(const language of ['en','zh-CN'])for(const focus of [false,true]){
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message))
  await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
  await page.goto(server.resolvedUrls.local[0]+'__runtime-reads');await page.waitForFunction(()=>typeof window.show==='function')
  await page.evaluate(args=>window.show(...args),[true,focus,language])
  if(focus)await page.waitForFunction(()=>window.reads.some(r=>r.kind==='job'))
  else{
   await page.locator('[data-terminal-id="terminal"] > button[aria-expanded]').click()
   await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.components.inspector.RuntimeTab.a748cc074f78')),exact:true}).click()
   await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.components.inspector.RuntimeTab.d70cf6ebc7af')),exact:true}).click()
   const more=page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.showMore')),exact:true})
   for (const index of [1, 0]) await more.nth(index).evaluate(button => {
    const props = button[Object.keys(button).find(key => key.startsWith('__reactProps$'))]
    props.onClick(); props.onClick()
   })
   assert.equal(await page.evaluate(()=>window.reads.filter(r=>r.kind==='agentHistory').length),1)
   assert.equal(await page.evaluate(()=>window.reads.filter(r=>r.kind==='jobHistory').length),1)
   await page.waitForFunction(()=>window.reads.length===5)
  }
  await page.evaluate(()=>{window.hold=true});await page.clock.runFor(2000)
  await page.waitForFunction(()=>window.reads.some(r=>r.kind==='runtime'))
  await page.evaluate(args=>window.show(...args),[false,focus,language])
  await page.waitForFunction(()=>window.reads.every(r=>r.signal?.aborted))
  const count=await page.evaluate(()=>window.reads.length)
  await page.clock.runFor(10000);assert.equal(await page.evaluate(()=>window.reads.length),count)
  await page.evaluate(()=>window.reads.forEach(r=>r.resolve({ok:false,message:'STALE READ'})))
  await page.clock.runFor(32);assert.equal(await page.getByText('STALE READ',{exact:true}).count(),0)
  await page.evaluate(args=>{window.hold=false;window.show(...args)},[true,focus,language])
  await page.waitForFunction(count=>window.reads.length>count,count)
  for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(theme=>document.documentElement.className=theme,theme)
   assert.equal(await page.getByText('STALE READ',{exact:true}).count(),0)
  }
  await page.evaluate(()=>window.reopen())
  await page.waitForFunction(()=>window.reads.every(r=>r.signal?.aborted))
  const closedCount=await page.evaluate(()=>window.reads.length)
  await page.evaluate(args=>window.show(...args),[true,focus,language])
  if(!focus)await page.locator('[data-terminal-id="terminal"] > button[aria-expanded]').click()
  await page.waitForFunction(count=>window.reads.length>count,closedCount)
  await page.evaluate(count=>window.reads.slice(0,count).forEach(r=>r.resolve({ok:false,message:'UNMOUNTED READ'})),closedCount)
  await page.clock.runFor(32)
  assert.equal(await page.getByText('UNMOUNTED READ',{exact:true}).count(),0)
  assert.deepEqual(errors,[]);await page.close()
 }
 console.log('Runtime terminal/member/job/focused-job/history reads: cancel on hide, ignore stale results, resume and preserve locale/theme/width matrix')
},{entry:'/__runtime-reads-entry.js',vite:{plugins:[{
 name:'runtime-reads-fixture',resolveId(id){if(id==='/__runtime-reads-entry.js')return id},load(id){if(id==='/__runtime-reads-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__runtime-reads',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__runtime-reads','<html><body><div id="root"></div><script type="module" src="/__runtime-reads-entry.js"></script></body></html>'))}catch(e){next(e)}})}
}]}})

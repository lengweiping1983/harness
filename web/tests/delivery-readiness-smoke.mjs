import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {TurnResults} from '/src/components/chat/blocks/TurnResults.tsx'
import {ReadinessCheck} from '/src/components/chat/ReadinessCheck.tsx'
import {SaveStatus} from '/src/components/ui/SaveStatus.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.copy=t;window.locale=setLocale;window.actions=[]
const root=createRoot(document.getElementById('root'))
window.configure=(verification,current=true)=>{
 useAppStore.setState({activeSessionId:current?'owner':'other',sessions:[],workspace:{root:'/fixture'},status:null,
 revealTurnChanges:(sessionId,turn)=>window.actions.push({sessionId,turn}),revealRun:(sessionId,target)=>window.actions.push({sessionId,...target,kind:'run'}),revealInFiles:(path,_line,source)=>window.actions.push({path,...source,kind:'file'})})
 root.render(React.createElement(React.Fragment,null,React.createElement(ReadinessCheck),
 ...['unsaved','saving','saved','failed','unknown'].map(phase=>React.createElement(SaveStatus,{key:phase,phase})),
 React.createElement(TurnResults,{sessionId:'owner',loading:false,onRefresh:()=>window.actions.push({refresh:true}),data:{turn:3,
 delivery:{tool_executions:5,artifact_count:3,failed_tools:1,unknown_tools:1,verification,
 artifacts:[{path:'/original/报告.md',source_seq:2,workspace_root:'/original',description:'Original description 原文'}, {path:'/unknown/result.txt',source_seq:4}],
 checks:verification==='unverified'?[]:[{source_seq:8,result_seq:7,check_id:'Original check 原文',paths:['original.py'],status:verification}]}}})))
}
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(), errors=[],requests=[];page.on('pageerror',e=>errors.push(e.message))
 await page.route('**/api/v1/**',route=>{requests.push(route.request().method());return route.fulfill({status:503,json:{error:'Fixture offline'}})})
 await page.goto(server.resolvedUrls.local[0]+'__delivery-readiness')
 for(const verification of ['unverified','passed','failed','stale','unknown']){
  await page.evaluate(value=>window.configure(value),verification)
  const results=page.locator('[data-turn-results="3"]');if(await results.getAttribute('open')===null)await results.locator('summary').click()
  for(const locale of ['en','zh-CN','en'])for(const width of [390,1440])for(const size of [10,22])for(const dark of [false,true]){
   await page.setViewportSize({width,height:1000});await page.evaluate(({locale,size,dark})=>{window.locale(locale);document.documentElement.style.fontSize=size+'px';document.documentElement.classList.toggle('dark',dark)},{locale,size,dark})
   const copy=async key=>page.evaluate(key=>window.copy(key),key)
   await results.getByText(await copy('ui.delivery.'+verification),{exact:true}).waitFor()
   await results.getByText(await copy('ui.delivery.readSnapshot'),{exact:true}).waitFor()
   if(verification!=='unverified')await results.getByText('Original check 原文',{exact:false}).waitFor()
   assert.ok(await page.locator('body').evaluate(node=>node.scrollWidth<=innerWidth+1))
  }
  await results.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.delivery.changes')),exact:true}).click()
  assert.deepEqual(await page.evaluate(()=>window.actions.at(-1)),{sessionId:'owner',turn:3})
  await results.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.delivery.execution')),exact:true}).click()
  assert.deepEqual(await page.evaluate(()=>window.actions.at(-1)),{sessionId:'owner',turn:3,kind:'run'})
  const originalFile=results.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.delivery.openArtifact',{path:'/original/报告.md'})),exact:true})
  await originalFile.click()
  assert.deepEqual(await page.evaluate(()=>window.actions.at(-1)),{path:'报告.md',root:'/original',kind:'file'})
  assert.equal(await results.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.delivery.openArtifact',{path:'/unknown/result.txt'})),exact:true}).isEnabled(),false)
  await results.getByText('Original description 原文',{exact:true}).waitFor()
  await results.getByText(await page.evaluate(()=>window.copy('ui.delivery.artifactsLimited',{shown:2,total:3})),{exact:true}).waitFor()
  await results.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.delivery.recheck')),exact:true}).click()
  assert.deepEqual(await page.evaluate(()=>window.actions.at(-1)),{refresh:true})
  await page.evaluate(value=>window.configure(value,false),verification)
  assert.equal(await results.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.delivery.changes')),exact:true}).isEnabled(),false)
  assert.equal(await results.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.delivery.execution')),exact:true}).isEnabled(),false)
  assert.equal(await originalFile.isEnabled(),false)
 }
 await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.readiness.dismiss')),exact:true}).click()
 assert.equal(await page.getByRole('region',{name:await page.evaluate(()=>window.copy('ui.readiness.title')),exact:true}).count(),0)
 assert.deepEqual(errors,[]);assert.equal(requests.length,0)
 await page.close()
 console.log('Delivery states, reading snapshot, owner-bound actions, readiness dismissal and save states across bilingual theme/size layouts passed')
},{entry:'/__delivery-readiness-entry.js',vite:{plugins:[{name:'delivery-readiness',resolveId(id){if(id==='/__delivery-readiness-entry.js')return id},load(id){if(id==='/__delivery-readiness-entry.js')return entry},configureServer(server){server.middlewares.use('/__delivery-readiness',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__delivery-readiness','<html><body><div id="root"></div><script type="module" src="/__delivery-readiness-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})

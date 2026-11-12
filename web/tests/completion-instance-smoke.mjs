import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {TurnCompletionBar} from '/src/components/chat/blocks/TurnCompletionBar.tsx'
import {UserBlock} from '/src/components/chat/blocks/MessageBlocks.tsx'
import {api} from '/src/lib/api.ts'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.copy=t;window.locale=setLocale;window.toasts=[];window.requests=[]
useAppStore.setState({activeSessionId:'owner',toast:value=>window.toasts.push(value)})
api.controlGoal=()=>new Promise(resolve=>{window.rejectGoal=()=>resolve({ok:false,status:500,message:'Original failure',copy:{key:'ui.http.invalidResponse'}})})
const root=createRoot(document.getElementById('root'))
window.show=(owner='owner',turn=1,user=false)=>root.render(user?React.createElement(UserBlock,{sessionId:owner,block:{kind:'user',id:'user',sourceSeq:1,text:'Original text',at:1}}):React.createElement(TurnCompletionBar,{sessionId:owner,row:{kind:'completion',id:'completion',at:1,turn,endSeq:turn*10,finalSeq:turn*10-1}}))
window.show()
`
await browserScenario(async({server,browser})=>{
 for(const scenario of ['pagination','pending','goal']){
  const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message))
  let release,started;const entered=new Promise(resolve=>started=resolve)
  const requests=[]
  let aborted=false
  page.on('requestfailed',request=>{if(new URL(request.url()).pathname.endsWith('/feedback'))aborted=true})
  await page.route('**/api/v1/**',async route=>{
   const url=new URL(route.request().url()),turn=Number(url.pathname.match(/turns\/(\d+)/)?.[1]??1)
   requests.push({path:url.pathname,offset:Number(url.searchParams.get('offset'))})
   if(url.pathname.endsWith('/feedback')){started();await new Promise(resolve=>release=resolve);try{await route.fulfill({json:{end_seq:10,feedback:'positive'}})}catch(error){assert.ok(aborted, String(error))}return}
   if(url.pathname.endsWith('/completion'))return route.fulfill({json:{turn,end_seq:turn*10,final_response_seq:turn*10-1,response_seq:turn*10-1,ended_at:1,feedback:null,delivery:{run_id:'fixture',start_seq:0,tool_executions:0,failed_tools:0,unknown_tools:0,artifacts:[],artifact_count:0,verification:'unverified',checks:[]},hooks:{total:51,runs:0,blocked:0,unsuccessful:0,counts:{},duration_ms:0,rows:[],offset:Number(url.searchParams.get('offset')),next_offset:url.searchParams.get('offset')==='50'?null:50}}})
   throw Error('Unexpected '+url)
  })
  await page.goto(server.resolvedUrls.local[0]+'__completion-instance')
  await page.waitForFunction(()=>document.querySelector('[data-turn-completion]')?.getAttribute('aria-busy')==='false')
  if(scenario==='goal'){
   await page.evaluate(()=>window.show('owner',1,true))
   await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.goal.setFromMessage')),exact:true}).click()
   await page.waitForFunction(()=>typeof window.rejectGoal==='function')
   await page.evaluate(()=>window.show('other',1,true))
   await page.waitForFunction(()=>Array.from(document.querySelectorAll('button')).some(button=>button.getAttribute('aria-label')===window.copy('ui.goal.setFromMessage')&&!button.disabled))
   await page.evaluate(()=>window.rejectGoal())
   await page.waitForTimeout(30)
   assert.equal(await page.evaluate(()=>window.toasts.length),0)
  }else{
   if(scenario==='pagination'){
    await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.components.chat.blocks.TurnCompletionBar.909ee3161e8a')),exact:true}).click()
    await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.components.chat.blocks.HookStatisticsDialog.8a8542f69648')),exact:true}).click()
    await page.waitForFunction(()=>document.querySelector('[data-turn-completion]')?.getAttribute('aria-busy')==='false')
   }else{
    await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.components.chat.blocks.TurnCompletionBar.28030e690447')),exact:true}).click()
    await entered
   }
   await page.evaluate(()=>window.show('other',2))
   await page.waitForFunction(()=>document.querySelector('[data-turn-completion="2"]')?.getAttribute('aria-busy')==='false')
   assert.equal(requests.filter(r=>r.path.includes('/other/turns/2/completion')).at(-1).offset,0)
   assert.equal(await page.getByRole('dialog').count(),0)
   if(scenario==='pending')release()
   for(const locale of ['en','zh-CN','en'])for(const width of [390,1440]){
    await page.setViewportSize({width,height:900});await page.evaluate(locale=>window.locale(locale),locale)
    assert.equal(await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.components.chat.blocks.TurnCompletionBar.28030e690447')),exact:true}).isEnabled(),true)
   }
  }
  assert.deepEqual(errors,[]);await page.close()
 }
 console.log('Completion/UserBlock instance changes isolate pagination, pending action and late goal errors')
},{entry:'/__completion-instance-entry.js',vite:{plugins:[{
 name:'completion-instance-fixture',resolveId(id){if(id==='/__completion-instance-entry.js')return id},load(id){if(id==='/__completion-instance-entry.js')return entry},configureServer(server){server.middlewares.use('/__completion-instance',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__completion-instance','<html><body><div id="root"></div><script type="module" src="/__completion-instance-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})

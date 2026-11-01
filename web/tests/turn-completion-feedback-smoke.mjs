import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const hook={id:'hook',root:'/fixture',session_id:'owner',call_id:'call',tool:'FixtureTool',label:'Command 原文',reason:'',event:'PreToolUse',status:'passed',turn:1,step:1,source_seq:1,duration_ms:12,started:1,finished:2}
const completion={turn:1,end_seq:9,final_response_seq:8,response_seq:8,ended_at:1,feedback:null,delivery:{run_id:'fixture',start_seq:0,tool_executions:0,failed_tools:0,unknown_tools:0,artifacts:[],artifact_count:0,verification:'unverified',checks:[]},hooks:{total:51,runs:1,blocked:0,unsuccessful:0,counts:{skipped:2},duration_ms:12,rows:[hook],offset:0,next_offset:50}}
const prefix='ui.components.chat.blocks.TurnCompletionBar.'
const entry=`
import React,{useState} from 'react'
import {createRoot} from 'react-dom/client'
import {TurnCompletionBar} from '/src/components/chat/blocks/TurnCompletionBar.tsx'
import {Toaster} from '/src/components/overlays/CommandPalette.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;window.copies=[];window.active=()=>useAppStore.getState().activeSessionId
const view=new URLSearchParams(location.search).get('view')
useAppStore.setState({activeSessionId:'owner',sessions:[{id:'owner',title:'Original conversation 原文'}],toasts:[]})
Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{if(view==='copy-failure')throw Error('Denied');window.copies.push(text)}}})
document.execCommand=()=>false
function Fixture(){const [visible,setVisible]=useState(true);window.hide=()=>setVisible(false);return React.createElement(React.Fragment,null,visible?React.createElement(TurnCompletionBar,{key:'owner:9',sessionId:'owner',row:{kind:'completion',id:'completion',at:1,turn:1,endSeq:9,finalSeq:8}}):null,React.createElement(Toaster))}
createRoot(document.getElementById('root')).render(React.createElement(Fixture))
`
await browserScenario(async({server,browser})=>{
 for(const view of ['read','identity','hooks','hooks-skipped','copy-failure','copy-success','feedback-mismatch','fork-json','fork-schema','fork-408','fork-503','fork-rejected','fork-late-close','fork-success']){
  const page=await browser.newPage();page.setDefaultTimeout(15000)
  await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
  const requests=[],errors=[];page.on('pageerror',error=>errors.push(error.message))
  let release,entered;const held=new Promise(resolve=>release=resolve),started=new Promise(resolve=>entered=resolve)
  await page.route('**/api/v1/**',async route=>{
   const req=route.request(),url=new URL(req.url());requests.push({method:req.method(),path:url.pathname,body:req.postDataJSON()})
   if(url.pathname.endsWith('/completion')){
    if(view==='read')return route.fulfill({body:'broken',contentType:'application/json'})
    const offset=Number(url.searchParams.get('offset')),value=structuredClone(completion)
    value.hooks.offset=offset;value.hooks.next_offset=offset?null:50
    if(view==='identity')value.end_seq=10
    if(view==='hooks')value.hooks.counts.skipped=0
    return route.fulfill({json:value})
   }
   if(url.pathname.endsWith('/messages/8')){
    const text='Reply 原文';return route.fulfill({json:{session_id:'owner',source_seq:8,source_time:1,role:'assistant',text,start:0,end:Array.from(text).length,total_chars:Array.from(text).length,previous_start:null,next_start:null}})
   }
   if(url.pathname.endsWith('/feedback'))return route.fulfill({json:{end_seq:10,feedback:'positive'}})
   if(url.pathname.endsWith('/fork')){
    if(view==='fork-late-close'){entered();await held}
    if(view==='fork-rejected')return route.fulfill({status:400,json:{code:'fixture/rejected',message:'Original refusal 原文'}})
    if(view==='fork-408'||view==='fork-503')return route.fulfill({status:view==='fork-408'?408:503,json:{code:'fixture/unknown',message:'Original failure 原文'}})
    if(view==='fork-schema')return route.fulfill({status:201,json:{session:{}}})
    if(view==='fork-success')return route.fulfill({status:201,json:{session:{id:'child'}}})
    return route.fulfill({status:201,body:'broken',contentType:'application/json'})
   }
   if(url.pathname==='/api/v1/sessions')return route.fulfill({json:{sessions:[{id:'owner',title:'Original conversation 原文'},{id:'child',title:'Branch 原文'}]}})
   throw Error('Unexpected completion request '+url)
  })
  await page.goto(server.resolvedUrls.local[0]+'__turn-completion-feedback?view='+view)
  const named=async suffix=>page.getByRole('button',{name:await page.evaluate(key=>window.copy(key),prefix+suffix),exact:true})
  await page.locator('[data-turn-completion]').waitFor()
  await page.waitForFunction(()=>document.querySelector('[data-turn-completion]').getAttribute('aria-busy')==='false')
  if(view.startsWith('hooks')){
   await (await named('909ee3161e8a')).click()
   await page.getByRole('dialog').waitFor()
   await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.components.chat.blocks.HookStatisticsDialog.8a8542f69648')),exact:true}).click()
  }
  if(view.startsWith('copy'))await (await named('d784b8b766ea')).click()
  if(view==='feedback-mismatch')await (await named('28030e690447')).click()
  if(view.startsWith('fork')){
   const listed=view==='fork-rejected'?null:page.waitForResponse(response=>new URL(response.url()).pathname==='/api/v1/sessions')
   await (await named('e13f6ee9b3ab')).click()
   if(view==='fork-late-close'){await started;await page.evaluate(()=>window.hide());await page.locator('[data-turn-completion]').waitFor({state:'detached'});release()}
   if(listed)await listed
  }
  const expected=()=>page.evaluate(({view,prefix})=>{
   if(view==='identity'||view==='feedback-mismatch')return window.copy(prefix+'866862db3616')
   if(view==='copy-failure')return window.copy(prefix+'2e9a64f29c7e')
   if(view==='copy-success')return window.copy(prefix+'8c039c6a861e')
   if(view==='fork-rejected')return 'Original refusal 原文'
   if(view.startsWith('fork')&&!['fork-success','fork-late-close'].includes(view))return window.copy('ui.turnCompletion.forkUnconfirmed',{reason:view==='fork-408'||view==='fork-503'?'Original failure 原文':window.copy('ui.http.invalidResponse')})
   return window.copy('ui.http.invalidResponse')
  },{view,prefix})
  const check=async()=>{
   if(view.startsWith('hooks')){
    const texts=await page.evaluate(view=>[window.copy(view==='hooks-skipped'?'ui.hookStatistics.summarySkipped':'ui.hookStatistics.summary',{turn:1,duration:'12ms',skipped:2}),window.copy('ui.hookStatistics.pagination',{start:51,end:51,total:51})],view)
    for(const text of texts)await page.getByText(text,{exact:true}).waitFor()
   }else if(view==='fork-success')await page.waitForFunction(()=>window.active()==='child')
   else if(view!=='fork-late-close')await page.getByText(await expected(),{exact:true}).waitFor()
  }
  await check();const count=requests.length
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
   await check();assert.equal(requests.length,count,'language must not repeat reads or writes')
   if(view.startsWith('fork')&&!['fork-rejected','fork-late-close','fork-success'].includes(view)){
    assert.ok(await (await named('e13f6ee9b3ab')).isDisabled())
    assert.equal(await (await named('fa660d296961')).count(),0)
   }
  }
  if(view==='fork-rejected'){
   await (await named('fa660d296961')).click();await page.waitForFunction(()=>document.querySelector('[data-turn-completion]').getAttribute('aria-busy')==='false');await check()
  }
  const writes=requests.filter(r=>r.method==='POST')
  assert.equal(writes.length,view==='fork-rejected'?2:view.startsWith('fork')||view==='feedback-mismatch'?1:0)
  if(view.startsWith('fork'))for(const write of writes){assert.equal(write.path,'/api/v1/sessions/owner/fork');assert.deepEqual(write.body,{through_seq:9})}
  assert.equal(requests.filter(r=>r.path==='/api/v1/sessions').length,view.startsWith('fork')&&view!=='fork-rejected'?1:0)
  if(view==='copy-success')assert.deepEqual(await page.evaluate(()=>window.copies),['Reply 原文'])
  if(view==='copy-failure')assert.deepEqual(await page.evaluate(()=>window.copies),[])
  if(view==='fork-late-close')assert.equal(await page.evaluate(()=>window.active()),'owner')
  assert.deepEqual(errors,[],view);await page.close();console.log(view+': resource language, current turn identity and write ownership verified')
 }
},{entry:'/__turn-completion-feedback-entry.js',vite:{plugins:[{
 name:'turn-completion-feedback-fixture',resolveId(id){if(id==='/__turn-completion-feedback-entry.js')return id},load(id){if(id==='/__turn-completion-feedback-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__turn-completion-feedback',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__turn-completion-feedback','<html><body><div id="root"></div><script type="module" src="/__turn-completion-feedback-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})

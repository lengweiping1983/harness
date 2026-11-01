import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React,{useState} from 'react'
import {createRoot} from 'react-dom/client'
import {CapabilitiesPanel} from '/src/components/overlays/CapabilitiesPanel.tsx'
import {useAppStore} from '/src/store/app.ts'
import {usePermissionStore} from '/src/store/permissions.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;window.app=useAppStore
useAppStore.setState({toasts:[]});usePermissionStore.setState({sessionId:null})
function Fixture(){const [visible,setVisible]=useState(true);window.hide=()=>setVisible(false);return visible?React.createElement(CapabilitiesPanel):null}
createRoot(document.getElementById('root')).render(React.createElement(Fixture))
`
const initial=()=>({permission_policy:'request_approval',context_auto_manage:true,context_working_budget:10000,summary_max_tokens:null,output_reserve:1000,context_window:12000,tool_result_max_chars:null,tool_schema_mode:'auto',max_steps:10,token_budget:null,network_timeout:60})
await browserScenario(async({server,browser})=>{
 for(const mode of ['readback','same-value','language','failure','unmount']){
  const page=await browser.newPage(),errors=[],requests=[];page.on('pageerror',error=>errors.push(error.message))
  let config=initial(),release,enter
  const held=new Promise(resolve=>release=resolve),started=new Promise(resolve=>enter=resolve)
  await page.route('**/api/v1/settings',async route=>{
   const request=route.request();requests.push({method:request.method(),body:request.postDataJSON()})
   if(request.method()==='PATCH'){
    enter();await held
    if(mode==='failure')return route.fulfill({status:403,json:{code:'access/denied',message:'Old snapshot'}})
    const accepted={...config,...request.postDataJSON()}
    config={...accepted,max_steps:mode==='same-value'?10:12}
    return route.fulfill({json:{config:accepted,effective:{model:'FixtureModel',context_window:12000,max_tokens:2000,sources:['manual','provider']},tools:[],skills:[]}})
   }
   return route.fulfill({json:{config,effective:{model:'FixtureModel',context_window:12000,max_tokens:2000,sources:['manual','provider']},tools:[],skills:[]}})
  })
  await page.goto(server.resolvedUrls.local[0]+'__capabilities-readback')
  const copy=key=>page.evaluate(key=>window.copy(key),key)
  const step=page.getByRole('spinbutton',{name:await copy('ui.components.overlays.CapabilitiesPanel.a4e6d1de3d04'),exact:true})
  for(const locale of ['en','zh-CN','en']){
   await page.evaluate(locale=>window.locale(locale),locale)
   await page.getByText(await copy('ui.components.overlays.CapabilitiesPanel.cdf5a33e8019'),{exact:true}).waitFor()
   assert.equal(await page.getByRole('spinbutton').count(),3)
   for(const name of ['单次生成时长上限（秒）','单次输出 Token 上限','模型响应字节上限','Generation time limit per attempt (seconds)','Per-response Token limit','Model response byte limit'])assert.equal(await page.getByRole('spinbutton',{name,exact:true}).count(),0)
  }
  await step.fill('17');await step.press('Tab');await started
  const reads=requests.filter(row=>row.method==='GET').length
  await page.getByText(await copy('ui.capabilities.limitSaveHint'),{exact:true}).waitFor()
  if(mode==='language'||mode==='failure')for(const locale of ['en','zh-CN','en']){
   await page.evaluate(locale=>window.locale(locale),locale)
   await page.getByText(await copy('ui.capabilities.limitSaveHint'),{exact:true}).waitFor()
   assert.equal(await page.locator('dl').count(),0)
   const current=page.getByRole('spinbutton',{name:await copy('ui.components.overlays.CapabilitiesPanel.a4e6d1de3d04'),exact:true})
   assert.equal(await current.inputValue(),'17')
   assert.equal(await current.isDisabled(),true)
   assert.equal(requests.filter(row=>row.method==='GET').length,reads)
   assert.equal(requests.filter(row=>row.method==='PATCH').length,1)
  }
  if(mode==='unmount'){
   await page.evaluate(()=>window.hide());await step.waitFor({state:'detached'})
   const response=page.waitForResponse(response=>response.url().endsWith('/api/v1/settings')&&response.request().method()==='PATCH')
   release();await (await response).finished()
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))))
   assert.equal(requests.filter(row=>row.method==='GET').length,reads)
   assert.equal(await page.evaluate(()=>window.app.getState().toasts.length),0)
  }else{
   release()
   await page.waitForFunction(label=>{const node=[...document.querySelectorAll('input')].find(node=>node.getAttribute('aria-label')===label);return node&&!node.disabled},await copy('ui.components.overlays.CapabilitiesPanel.a4e6d1de3d04'))
   if(mode==='failure'){
    for(const locale of ['en','zh-CN','en']){
     await page.evaluate(locale=>window.locale(locale),locale)
     const expected=await page.evaluate(()=>window.copy('ui.collaboration.error.access/denied',{code:'access/denied'}))
     await page.getByRole('alert').filter({hasText:expected}).waitFor()
     assert.equal(await page.getByRole('spinbutton',{name:await copy('ui.components.overlays.CapabilitiesPanel.a4e6d1de3d04'),exact:true}).inputValue(),'17')
    }
   }else assert.equal(await page.getByRole('spinbutton',{name:await copy('ui.components.overlays.CapabilitiesPanel.a4e6d1de3d04'),exact:true}).inputValue(),mode==='same-value'?'10':'12')
  }
  assert.deepEqual(requests.filter(row=>row.method==='PATCH').map(row=>row.body),[{max_steps:17}])
  assert.deepEqual(errors,[]);await page.close()
 }
 console.log('Capability writes: authoritative readback updates numeric fields, language waits for writes, failed drafts retain resources, closed instances stay quiet')
},{entry:'/__capabilities-readback-entry.js',vite:{plugins:[{name:'capabilities-readback',resolveId(id){if(id==='/__capabilities-readback-entry.js')return id},load(id){if(id==='/__capabilities-readback-entry.js')return entry},configureServer(server){server.middlewares.use('/__capabilities-readback',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__capabilities-readback','<html><head><link rel="icon" href="data:,"></head><body><div id="root"></div><script type="module" src="/__capabilities-readback-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})

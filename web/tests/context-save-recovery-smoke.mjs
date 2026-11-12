import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const entry = `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ContextSettingsPanel} from '/src/components/overlays/ContextSettingsPanel.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;window.calls=[];window.readFailures=0;window.writeMode='unknown'
let config={context_auto_manage:true,context_working_budget:10000,tool_result_max_chars:null,tool_schema_mode:'auto',summary_max_tokens:null,output_reserve:1000,context_window:12000}
const response=()=>Response.json({config:{...config},effective:{context_window:12000}})
window.notify=budget=>{config={...config,context_working_budget:budget};window.dispatchEvent(new CustomEvent('harness-context-policy-saved',{detail:{config:{...config},effective:{context_window:12000}}}))}
window.fetch=async(path,options={})=>{
 const method=options.method||'GET';window.calls.push({method,body:options.body})
 if(method==='PATCH'){
  if(window.writeMode==='refuse')return Response.json({code:'settings/invalid',message:'Original refusal'},{status:400})
  config={...config,...JSON.parse(options.body)};window.readFailures=1
  return Response.json({message:'Original uncertain response'},{status:503})
 }
 if(window.calls.length===1&&location.search){const snapshot=response();await new Promise(resolve=>{window.releaseInitial=resolve});return snapshot}
 if(window.readFailures>0){--window.readFailures;return Response.json({message:'Original read failure'},{status:503})}
 return response()
}
const root=createRoot(document.getElementById('root'));let instance=0
window.mount=()=>root.render(React.createElement(ContextSettingsPanel,{key:++instance}))
window.close=()=>root.render(null)
window.mount()
`

await browserScenario(async ({server,browser}) => {
 const page=await browser.newPage(), errors=[]
 page.on('pageerror',error=>errors.push(error.message))
 const url=server.resolvedUrls.local[0]+'__context-save-recovery'
 const copy=key=>page.evaluate(key=>window.copy(key),key)
 const button=async key=>page.getByRole('button',{name:await copy(key),exact:true})
 const reserve=async()=>page.getByRole('spinbutton',{name:await copy('ui.context.output_reserve'),exact:true})
 const advanced=async()=>page.getByText(await copy('ui.context.advanced'),{exact:true}).click()
 const patches=()=>page.evaluate(()=>window.calls.filter(call=>call.method==='PATCH').length)

 await page.goto(url+'?initial=held')
 await page.waitForFunction(()=>typeof window.releaseInitial==='function')
 await page.evaluate(()=>window.notify(4800))
 await advanced();assert.equal(await (await reserve()).inputValue(),'1000')
 assert.equal(await page.getByRole('button',{name:await copy('ui.context.preset.efficient'),exact:false}).getAttribute('aria-pressed'),'true')
 await page.evaluate(()=>window.releaseInitial())
 assert.equal(await (await reserve()).inputValue(),'1000','An unowned notification must supersede the initial read')
 assert.equal(await page.getByRole('button',{name:await copy('ui.context.preset.efficient'),exact:false}).getAttribute('aria-pressed'),'true')
 assert.equal(await patches(),0)

 await page.goto(url)
 await page.getByRole('button',{name:await copy('ui.context.preset.efficient'),exact:false}).click()
 await advanced();await (await reserve()).fill('2000')
 await (await button('ui.context.save')).click()
 await (await button('ui.context.recheckSave')).waitFor()
 await (await reserve()).fill('1000')
 await page.evaluate(()=>window.notify(32000))
 assert.equal(await (await reserve()).inputValue(),'1000','A same-value edit is still owned by the user')
 for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440])for(const font of [10,22]){
  await page.setViewportSize({width,height:1000})
  await page.evaluate(({locale,dark,font})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark);document.documentElement.style.fontSize=font+'px'},{locale,dark,font})
  await (await button('ui.context.recheckSave')).waitFor()
  assert.equal(await (await reserve()).inputValue(),'1000')
  assert.equal(await patches(),1)
  assert.equal(await page.locator('[data-save-phase="unknown"]').count(),1)
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
 }
 await (await button('ui.context.recheckSave')).click()
 await (await button('ui.context.recheckSave')).waitFor()
 assert.equal(await patches(),1,'A mismatched read must not replay the original PATCH')
 await page.evaluate(()=>window.notify(4800))
 await (await button('ui.context.recheckSave')).click()
 await page.locator('[data-save-phase="unsaved"]').waitFor()
 assert.equal(await (await reserve()).inputValue(),'1000','Confirming the original intent must preserve later edits')
 assert.equal(await patches(),1)

 await page.evaluate(()=>{window.writeMode='refuse'})
 await (await button('ui.context.save')).click()
 await page.locator('[data-save-phase="failed"]').waitFor()
 const afterRefusal=await page.evaluate(()=>window.calls.length)
 assert.equal(await patches(),2)
 await (await reserve()).fill('11000')
 await page.locator('[data-save-phase="unsaved"]').waitFor()
 assert.equal(await page.evaluate(()=>window.calls.length),afterRefusal,'Editing a rejected save must not send a request')

 await page.evaluate(()=>{window.writeMode='unknown'})
 await (await button('ui.context.save')).click()
 await (await button('ui.context.recheckSave')).waitFor()
 await page.evaluate(()=>window.close())
 await page.getByRole('heading').waitFor({state:'detached'})
 await page.evaluate(()=>{window.readFailures=1;window.mount()})
 await page.getByRole('alert').filter({hasText:'Original read failure'}).waitFor()
 await (await button('ui.context.recheckSave')).click()
 await page.locator('[data-save-phase="saved"]').waitFor()
 await advanced();assert.equal(await (await reserve()).inputValue(),'11000')
 assert.equal(await patches(),3,'Remount and read failure must preserve the one accepted write')
 assert.deepEqual(errors,[])
 await page.close()
 console.log('Context recovery: unknown saves only GET, notifications and same-value edits preserve ownership, explicit refusal recovers, remount can recheck despite failed loading; locale/theme/size/font matrix passed')
},{entry:'/__context-save-recovery-entry.js',vite:{plugins:[{
 name:'context-save-recovery-fixture',
 resolveId(id){if(id==='/__context-save-recovery-entry.js')return id},
 load(id){if(id==='/__context-save-recovery-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__context-save-recovery',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__context-save-recovery','<html><body><div id="root"></div><script type="module" src="/__context-save-recovery-entry.js"></script></body></html>'))}catch(error){next(error)}})},
}]}})

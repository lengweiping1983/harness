import assert from 'node:assert/strict'
import {mkdir} from 'node:fs/promises'
import {browserScenario} from './browser-scenario.mjs'
const entry=`import React from 'react';import {createRoot} from 'react-dom/client';import {ContextAuditPanel} from '/src/components/inspector/ContextAuditPanel.tsx';import {useAppStore} from '/src/store/app.ts';import {setLocale,t} from '/src/i18n/index.ts';import '/src/styles.css';setLocale('en');window.locale=setLocale;window.copy=t;useAppStore.setState({defaultModel:'global',sessions:[{id:'one',model:'selected'}],models:[{id:'global',label:'Global',context_window:2000000},{id:'selected',label:'Selected',context_window:1000000}]});window.capacity=value=>useAppStore.setState(state=>({models:state.models.map(model=>model.id==='selected'?{...model,context_window:value}:model)}));createRoot(document.getElementById('root')).render(React.createElement(ContextAuditPanel,{sessionId:'one'}));`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage();page.setDefaultTimeout(15000);const errors=[],writes=[];let refuse=false
 let config={context_auto_manage:true,context_working_budget:384000,tool_result_max_chars:null,tool_schema_mode:'auto',summary_max_tokens:null,output_reserve:16000,context_window:null}
 page.on('pageerror',error=>errors.push(error.message))
 await page.route('**/api/v1/**',route=>{
  const request=route.request(),path=new URL(request.url()).pathname
  if(path==='/api/v1/settings'){
   if(request.method()==='PATCH'){writes.push(request.postDataJSON());if(refuse)return route.fulfill({status:400,json:{message:'Fixture refusal'}});config={...config,...request.postDataJSON()}}
   return route.fulfill({json:{config,effective:{context_window:2000000}}})
  }
  if(path.endsWith('/context/compact'))return route.fulfill({json:null})
  if(path.endsWith('/context'))return route.fulfill({json:{session_id:'one',through:0,current:null,compactions:[],has_more:false,next_before:null}})
  errors.push(path);return route.fulfill({status:500,json:{message:'Unexpected fixture request'}})
 })
 await page.goto(server.resolvedUrls.local[0]+'__context-presets-model')
 await page.locator('[data-context-settings] > summary').click()
 const panel=page.locator('[data-context-settings]'),group=panel.getByRole('group'),cards=group.getByRole('button')
 const copy=(key,params)=>page.evaluate(({key,params})=>window.copy(key,params),{key,params})
 const button=async key=>panel.getByRole('button',{name:await copy(key),exact:true})
 const preset=async id=>group.getByRole('button',{name:await copy('ui.context.preset.'+id),exact:false})
 const check=async(budgets,selected)=>{
  for(let index=0;index<3;index++)await cards.nth(index).getByText(new RegExp(budgets[index].toLocaleString('en'))).waitFor()
  assert.equal(await group.getByRole('button',{pressed:true}).count(),1)
  assert.equal(await (await preset(selected)).getAttribute('aria-pressed'),'true')
  const text=await cards.allTextContents();for(let index=0;index<3;index++)assert.ok(text[index].includes(budgets[index].toLocaleString('en')))
 }
 await check([400000,600000,800000],'balanced');assert.equal(writes.length,0)
 assert.equal(await panel.getByRole('spinbutton').count(),0,'Advanced fields remain collapsed')
 assert.equal(await panel.getByText(/384,000/).count(),0,'No independent working budget contradicts the selected profile')
 await (await preset('efficient')).focus();await page.keyboard.press('Enter');await check([400000,600000,800000],'efficient');assert.equal(writes.length,0)
 await (await button('ui.context.save')).click();await panel.locator('[data-save-phase="saved"]').waitFor()
 assert.deepEqual(writes[0],{context_working_budget:400000,tool_result_max_chars:8000,summary_max_tokens:4096})
 await page.evaluate(()=>window.capacity(128000));await check([51200,76800,92800],'efficient');assert.equal(writes.length,1)
 await page.evaluate(()=>window.capacity(null));await check([128000,256000,384000],'efficient');assert.equal(writes.length,1)
 await (await preset('balanced')).click();await (await button('ui.context.save')).click();await panel.locator('[data-save-phase="saved"]').waitFor();await check([128000,256000,384000],'balanced')
 assert.equal(writes[1].context_working_budget,256000)
 refuse=true;await (await preset('extended')).click();await (await button('ui.context.save')).click();await page.getByRole('alert').filter({hasText:'Fixture refusal'}).waitFor();await check([128000,256000,384000],'extended')
 assert.equal(writes.length,3)
 await mkdir('../output/context-presets',{recursive:true})
 for(const language of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  await page.setViewportSize({width,height:1000});await page.evaluate(({language,theme})=>{window.locale(language);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{language,theme})
  assert.equal(await group.getByRole('button',{pressed:true}).count(),1)
  await panel.getByText(await copy('ui.context.presetFallback'),{exact:true}).waitFor();assert.equal(writes.length,3)
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false)
  if(language==='zh-CN'&&width===1440)await page.screenshot({path:`../output/context-presets/unknown-${theme}.png`,fullPage:true})
 }
 await page.evaluate(()=>window.capacity(1000000));await check([400000,600000,800000],'extended')
 await panel.getByText(await copy('ui.context.advanced'),{exact:true}).click()
 await panel.getByRole('spinbutton',{name:await copy('ui.context.context_window'),exact:true}).fill('64000');await check([25600,38400,38400],'extended')
 refuse=false;await (await button('ui.context.save')).click();await panel.locator('[data-save-phase="saved"]').waitFor()
 assert.equal(writes[3].context_working_budget,38400);assert.equal(writes[3].context_window,64000)
 await panel.getByText(await copy('ui.context.advanced'),{exact:true}).click()
 await page.evaluate(()=>{window.locale('zh-CN');document.documentElement.className='light';document.documentElement.dataset.theme='light'});await page.screenshot({path:'../output/context-presets/known-light.png',fullPage:true})
 assert.deepEqual(errors,[]);await page.close();console.log('Context presets: actual conversation model capacity, 40/60/80 targets with safety cap, 128/256/384K unknown fallback, one selected, lower manual cap, explicit saves, refusal/draft retention and bilingual theme/width matrix passed')
},{entry:'/__context-presets-model-entry.jsx',vite:{plugins:[{name:'context-presets-model-fixture',resolveId(id){if(id==='/__context-presets-model-entry.jsx')return id},load(id){if(id==='/__context-presets-model-entry.jsx')return entry},configureServer(server){server.middlewares.use('/__context-presets-model',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__context-presets-model','<html><body><div id="root"></div><script type="module" src="/__context-presets-model-entry.jsx"></script></body></html>'))}catch(error){next(error)}})}}]}})

import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const ids=['harness/supplier/endpoint/model-pro','harness/supplier/endpoint/model-flash']
const models=ids.map((id,index)=>({id,label:index?'DeepSeek V4.1 Flash':'DeepSeek V4 Pro',provider:'DeepSeek',visible:true,active:!index,context_window:1048576,supports_reasoning:false}))
const provider={id:'supplier',type:'deepseek',name:'DeepSeek',connections:[{id:'endpoint',name:'DeepSeek',base_url:'https://api.deepseek.com',protocol:'',enabled:true,auth:{type:'environment',env:{}},keys:[],models:[{id:'model-pro',model:'deepseek-v4-pro',name:'DeepSeek V4 Pro',enabled:true,input:['text']},{id:'model-flash',model:'deepseek-v4.1-flash',name:'DeepSeek V4.1 Flash',enabled:true,input:['text']}]}]}
const entry=`
import React from 'react';import {createRoot} from 'react-dom/client';import {SettingsDialog} from '/src/components/overlays/SettingsDialog.tsx';import {useAppStore} from '/src/store/app.ts';import {setLocale,t} from '/src/i18n/index.ts';import '/src/styles.css';
window.locale=setLocale;window.copy=t;window.state=useAppStore.getState;setLocale('en');useAppStore.setState({models:${JSON.stringify(models)},defaultModel:${JSON.stringify(ids[0])},settingsOpen:true,settingsTarget:'models'});createRoot(document.getElementById('root')).render(React.createElement(SettingsDialog));`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage();page.setDefaultTimeout(15000)
 const writes=[],errors=[];let settings={default_model:ids[0],model_visibility:{},deepseek_web_enabled:false,deepseek_web_api_key_configured:false,brave_web_enabled:false,brave_web_api_key_configured:false}
 page.on('pageerror',error=>errors.push(error.message))
 await page.route('**/api/v1/**',route=>{
  const request=route.request(),path=new URL(request.url()).pathname
  if(path==='/api/v1/ui-settings'){
   if(request.method()==='PATCH'){const body=request.postDataJSON();writes.push(body);settings={...settings,...body}}
   return route.fulfill({json:{settings}})
  }
  if(path==='/api/v1/models')return route.fulfill({json:{models,active:settings.default_model,providers:['DeepSeek']}})
  if(path==='/api/v1/connection')return route.fulfill({json:{version:2,revision:'fixture',providers:[provider],models:[],current:null,cycle:1}})
  if(path==='/api/v1/connection/providers')return route.fulfill({json:{providers:[]}})
  if(path==='/api/v1/codex-gateway')return route.fulfill({json:{accounts:[]}})
  errors.push(path);return route.fulfill({status:500,json:{error:'Unexpected fixture request'}})
 })
 await page.goto(server.resolvedUrls.local[0]+'__provider-labels')
 for(const language of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({language,theme})=>{window.locale(language);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{language,theme})
  const card=page.getByRole('region',{name:'DeepSeek',exact:true});await card.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.providers.edit')),exact:true}).waitFor();await card.getByText('DeepSeek V4 Pro',{exact:true}).waitFor();await card.getByText('DeepSeek V4.1 Flash',{exact:true}).waitFor();assert.equal(await card.getByRole('switch').count(),2)
  const text=await page.locator('body').innerText();assert.equal(text.includes('harness/'),false);const capacity=await page.evaluate(()=>window.copy('ui.models.contextSize',{count:'1.0M'}));assert.equal(await page.getByText(capacity,{exact:true}).count(),2)
  assert.equal(writes.length,0);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false)
 }
 await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.components.overlays.SettingsDialog.357211096f78')),exact:true}).click()
 await page.waitForFunction(id=>window.state().defaultModel===id,ids[1]);assert.equal(writes[0].default_model,ids[1])
 const hide=await page.evaluate(()=>window.copy('ui.settings.models.hideNamed',{model:'DeepSeek V4 Pro'}));await page.getByRole('switch',{name:hide,exact:true}).click()
 await page.waitForFunction(id=>window.state().modelVisibility[id]===false,ids[0]);assert.deepEqual(writes[1].model_visibility,{[ids[0]]:false})
 assert.deepEqual(errors,[]);await page.close();console.log('Model labels: internal IDs hidden, context preserved, stable IDs still used for defaults and visibility across language/theme/width matrix')
},{entry:'/__provider-labels-entry.jsx',vite:{plugins:[{name:'provider-labels-fixture',resolveId(id){if(id==='/__provider-labels-entry.jsx')return id},load(id){if(id==='/__provider-labels-entry.jsx')return entry},configureServer(server){server.middlewares.use('/__provider-labels',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__provider-labels','<html><body><div id="root"></div><script type="module" src="/__provider-labels-entry.jsx"></script></body></html>'))}catch(error){next(error)}})}}]}})

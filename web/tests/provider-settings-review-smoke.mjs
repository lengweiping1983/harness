import assert from 'node:assert/strict'
import {mkdir} from 'node:fs/promises'
import {browserScenario} from './browser-scenario.mjs'
const id='harness/supplier/endpoint/model'
const models=[{id,label:'DeepSeek V4 Pro',provider:'DeepSeek',visible:true,active:true,context_window:1048576,supports_reasoning:false}]
const entry=`import React from 'react';import {createRoot} from 'react-dom/client';import {SettingsDialog} from '/src/components/overlays/SettingsDialog.tsx';import {useAppStore} from '/src/store/app.ts';import {setLocale,t} from '/src/i18n/index.ts';import '/src/styles.css';setLocale('en');window.locale=setLocale;window.copy=t;window.navigate=target=>useAppStore.setState({settingsTarget:target});window.catalog=()=>useAppStore.setState({models:${JSON.stringify(models)}});window.clearCatalog=()=>useAppStore.setState({models:[]});useAppStore.setState({models:${JSON.stringify(models)},defaultModel:${JSON.stringify(id)},settingsOpen:true,settingsTarget:'models'});createRoot(document.getElementById('root')).render(React.createElement(SettingsDialog));`
const config={context_auto_manage:true,context_working_budget:10000,summary_max_tokens:null,output_reserve:1000,context_window:1048576,tool_result_max_chars:null,tool_schema_mode:'auto',permission_policy:'request_approval',max_steps:10,token_budget:null,network_timeout:60}
const provider={id:'supplier',type:'deepseek',name:'DeepSeek',connections:[{id:'endpoint',name:'DeepSeek',base_url:'https://api.deepseek.com',protocol:'',enabled:true,auth:{type:'api_key',env:{}},keys:[{id:'key',enabled:true,available:true,mask:'******last'}],models:[{id:'model',model:'deepseek-v4-pro',name:'DeepSeek V4 Pro',enabled:true,input:['text'],context_window:1048576}]}]}
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage();page.setDefaultTimeout(15000)
 const errors=[],writes=[];page.on('pageerror',error=>errors.push(error.message))
 await page.route('**/api/v1/**',route=>{
  const request=route.request(),path=new URL(request.url()).pathname
  if(request.method()!=='GET'){writes.push(path);return route.fulfill({status:500,json:{error:'No writes expected'}})}
  if(path==='/api/v1/connection')return route.fulfill({json:{version:2,revision:'fixture',providers:[provider],models:[],current:null,cycle:1}})
  if(path==='/api/v1/connection/providers')return route.fulfill({json:{providers:[{id:'deepseek',name:'DeepSeek',base_url:'https://api.deepseek.com',api_key:true,oauth:false,env:[],models:[{id:'deepseek-v4-pro',name:'DeepSeek V4 Pro'}]}]}})
  if(path==='/api/v1/codex-gateway')return route.fulfill({json:{accounts:[]}})
  if(path==='/api/v1/models')return route.fulfill({json:{models,active:id,providers:['DeepSeek']}})
  if(path==='/api/v1/ui-settings')return route.fulfill({json:{settings:{default_model:id,model_visibility:{},deepseek_web_enabled:false,deepseek_web_api_key_configured:false,brave_web_enabled:false,brave_web_api_key_configured:false}}})
  if(path==='/api/v1/settings')return route.fulfill({json:{config,effective:{model:id,context_window:1048576,max_tokens:2000,sources:['configuration']},tools:[],skills:[]}})
  errors.push(path);return route.fulfill({status:500,json:{error:'Unexpected fixture request'}})
 })
 await page.goto(server.resolvedUrls.local[0]+'__provider-settings-review')
 await page.getByRole('button',{name:'Edit',exact:true}).click()
 await page.getByText('Model options',{exact:true}).click()
 await mkdir('../output/provider-review',{recursive:true})
 for(const language of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  await page.setViewportSize({width,height:1000});await page.evaluate(({language,theme})=>{window.locale(language);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{language,theme})
  const copy=await page.evaluate(()=>({supplier:window.copy('ui.providers.supplierName'),model:window.copy('ui.providers.modelName'),save:window.copy('ui.providers.save'),description:window.copy('ui.providers.description')}))
  const selectedModels=page.getByRole('group',{name:/models selected|已选择.*个模型/})
  await page.getByLabel(copy.supplier,{exact:true}).waitFor();await selectedModels.getByLabel(copy.model,{exact:true}).waitFor()
  assert.equal(await page.getByLabel(copy.supplier,{exact:true}).inputValue(),'DeepSeek');assert.equal(await selectedModels.getByLabel(copy.model,{exact:true}).inputValue(),'DeepSeek V4 Pro')
  if(language==='zh-CN')assert.equal(copy.description.includes('再'),false)
  const save=page.getByRole('button',{name:copy.save,exact:true});await save.scrollIntoViewIfNeeded()
  const geometry=await save.evaluate(button=>{
   const footer=button.parentElement,form=button.closest('form'),input=form.querySelector('input');const f=footer.getBoundingClientRect(),p=form.getBoundingClientRect(),b=button.getBoundingClientRect(),i=input.getBoundingClientRect();const style=getComputedStyle(form)
   return {left:Math.abs(f.left-p.left-parseFloat(style.borderLeftWidth)),right:Math.abs(p.right-f.right-parseFloat(style.borderRightWidth)),buttons:Math.abs(b.left-i.left),sameColor:getComputedStyle(footer).backgroundColor===style.backgroundColor}
  })
  assert.ok(geometry.left<=1&&geometry.right<=1&&geometry.buttons<=1,JSON.stringify(geometry));assert.equal(geometry.sameColor,true)
  assert.equal((await page.locator('body').innerText()).includes('harness/'),false)
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false)
  if(language==='zh-CN'&&width===1440){await page.screenshot({path:`../output/provider-review/models-${theme}.png`,fullPage:true});await page.getByLabel(copy.supplier,{exact:true}).scrollIntoViewIfNeeded();await page.screenshot({path:`../output/provider-review/models-top-${theme}.png`,fullPage:true})}
 }
 await page.locator('button[data-page="permissions"]').click()
 for(const language of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  await page.setViewportSize({width,height:1000});await page.evaluate(({language,theme})=>{window.locale(language);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{language,theme})
  await page.getByRole('combobox').waitFor();assert.equal(await page.locator('#settings-content dl').count(),0)
  assert.equal((await page.locator('body').innerText()).includes('harness/'),false)
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false)
  if(language==='zh-CN'&&width===1440)await page.screenshot({path:`../output/provider-review/permissions-${theme}.png`,fullPage:true})
 }
 await page.evaluate(()=>window.clearCatalog());assert.equal(await page.locator('#settings-content dl').count(),0);assert.equal((await page.locator('body').innerText()).includes('harness/'),false)
 assert.deepEqual(writes,[]);assert.deepEqual(errors,[]);await page.close();console.log('Five screenshot issues rechecked in actual SettingsDialog: provider/model labels, concise copy, flush footer geometry, removed permissions summary, empty catalog, language/theme/width matrix and zero writes passed')
},{entry:'/__provider-settings-review-entry.jsx',vite:{plugins:[{name:'provider-settings-review',resolveId(id){if(id==='/__provider-settings-review-entry.jsx')return id},load(id){if(id==='/__provider-settings-review-entry.jsx')return entry},configureServer(server){server.middlewares.use('/__provider-settings-review',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__provider-settings-review','<html><body><div id="root"></div><script type="module" src="/__provider-settings-review-entry.jsx"></script></body></html>'))}catch(error){next(error)}})}}]}})

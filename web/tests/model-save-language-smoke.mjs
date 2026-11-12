import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const models=[{id:'constructor',label:'Old model 原文',provider:'fixture',active:true,visible:true,context_window:12000,max_output:4000,supports_reasoning:true,reasoning_efforts:['low','high']},{id:'__proto__',label:'New model 原文',provider:'fixture',visible:true}]
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ModelMenu} from '/src/components/chat/ModelPicker.tsx'
import {SettingsDialog} from '/src/components/overlays/SettingsDialog.tsx'
import {Toaster} from '/src/components/overlays/CommandPalette.tsx'
import {useAppStore,modelSaveKey} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t;setLocale('en')
const models=${JSON.stringify(models)},view=new URLSearchParams(location.search).get('view')
useAppStore.setState({activeSessionId:'fixture',sessions:[{id:'fixture',model:'constructor',reasoning_effort:'low',metadata:{reasoning_effort:'low'}}],models,defaultModel:'constructor',settingsOpen:view.startsWith('settings'),settingsTarget:'models'})
if(view==='settings-pending')useAppStore.setState({modelSaves:{[modelSaveKey(null)]:true},defaultModelSave:{target:'__proto__',error:null,unconfirmed:false},modelVisibilitySaves:{['constructor']:{pending:true,target:false,error:null,unconfirmed:false},['__proto__']:{pending:true,target:true,error:null,unconfirmed:false}}})
const content=view.startsWith('settings')?React.createElement(SettingsDialog):React.createElement(ModelMenu,{models,current:'constructor',effort:'low',onClose:()=>{throw Error('A rejected model selection must remain open')}})
createRoot(document.getElementById('root')).render(React.createElement(React.Fragment,null,content,React.createElement(Toaster)))
`
await browserScenario(async({server,browser})=>{
 for(const view of ['menu-model','menu-reasoning','settings-default','settings-visibility','settings-pending','menu-provider']){
  const page=await browser.newPage();page.setDefaultTimeout(15000)
  await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
  const requests=[],errors=[];page.on('pageerror',error=>errors.push(error.message))
  await page.route('**/api/v1/**',route=>{
   const request=route.request(),url=new URL(request.url());requests.push({method:request.method(),path:url.pathname})
   if(request.method()==='PATCH')return route.fulfill({status:200,body:'not json',contentType:'application/json'})
   if(url.pathname.endsWith('/connection'))return route.fulfill({json:{version:2,revision:'fixture',providers:[],models:[],current:null,cycle:1}})
   if(url.pathname==='/api/v1/connection/providers')return route.fulfill({json:{providers:[]}})
   if(url.pathname==='/api/v1/codex-gateway')return route.fulfill({json:{enabled:false,threshold:95,waiting:0,accounts:[]}})
   if(url.pathname==='/api/v1/codex-gateway/desktop')return route.fulfill({json:{enabled:false,config_path:'/fixture/config.toml'}})
   if(url.pathname.endsWith('/chatgpt'))return route.fulfill({json:{logged_in:false,email:'',pending:false,error:'',selected:'',models:[]}})
   if(url.pathname.endsWith('/models'))return route.fulfill({json:{models,active:'constructor',providers:['fixture']}})
   if(url.pathname.endsWith('/ui-settings'))return route.fulfill({json:{settings:{model_visibility:{['__proto__']:true},deepseek_web_enabled:false,deepseek_web_api_key_configured:false,brave_web_enabled:false,brave_web_api_key_configured:false}}})
   if(url.pathname==='/api/v1/sessions/fixture')return route.fulfill({json:{header:{id:'fixture',metadata:{model:'constructor',reasoning_effort:'low'}},events:[],messages:[],usage:{},stats:{},todos:[],timeline:[]}})
   errors.push('Unexpected model request '+url);return route.fulfill({status:500,json:{error:'unexpected fixture request'}})
  })
  await page.goto(server.resolvedUrls.local[0]+'__model-save?view='+view)
  if(view==='menu-model')await page.getByRole('option').filter({hasText:'New model 原文'}).click()
  if(view==='menu-reasoning')await page.getByRole('button',{name:'high',exact:true}).click()
  if(view==='settings-default')await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.components.overlays.SettingsDialog.357211096f78')),exact:true}).click()
  if(view==='settings-visibility')await page.getByRole('switch',{name:await page.evaluate(()=>window.copy('ui.settings.models.hideNamed',{model:'New model 原文'})),exact:true}).click()
  const feedback=async(locale='en')=>{
   if(view==='settings-pending'){
    for(const [key,model] of [['savingDefault','New model 原文'],['hiding','Old model 原文'],['showing','New model 原文']])await page.getByText(await page.evaluate(({key,model})=>window.copy('ui.settings.models.'+key,{model}),{key,model}),{exact:true}).waitFor()
    return
   }
   if(view==='menu-provider'){
    for(const label of ['Old model 原文','New model 原文'])await page.getByRole('option').filter({hasText:label}).waitFor()
    assert.equal(await page.getByText('Global models · Cycle 2',{exact:true}).count(),0)
    return
   }
   const strings=await page.evaluate(view=>{
    const key=view==='menu-model'?'ui.components.chat.ModelPicker.5f93c9daad89':view==='menu-reasoning'?'ui.components.chat.ModelPicker.49fd236d4f7a':view==='settings-default'?'ui.components.overlays.SettingsDialog.fa8a07865d96':'ui.components.overlays.SettingsDialog.cc6f6f1dda23'
    const invalid=window.copy('ui.http.invalidResponse')
    return {label:window.copy(key),text:view==='menu-reasoning'?window.copy('ui.store.app.340d62606aa1',{p0:invalid}):invalid}
   },view)
   await page.getByRole('alert',{name:strings.label,exact:true}).getByText(strings.text,{exact:true}).waitFor()
   if(view==='settings-default'||view==='settings-visibility')await page.getByRole('button',{name:await page.evaluate(view=>window.copy('ui.settings.models.'+(view==='settings-default'?'retryDefault':'retryHide'),{model:'New model 原文'}),view),exact:true}).waitFor()
   if(view==='menu-model'){
    const texts=await page.evaluate(()=>({retry:window.copy('ui.models.retrySwitch',{model:'New model 原文'}),context:window.copy('ui.models.contextSize',{count:'12k'}),output:window.copy('ui.models.outputSize',{count:'4k'})}))
    assert.deepEqual(texts,locale==='zh-CN'?{retry:'重试切换到 New model 原文',context:'上下文：12k',output:'输出：4k'}:{retry:'Retry switching to New model 原文',context:'Context: 12k',output:'Output: 4k'})
    await page.getByRole('button',{name:texts.retry,exact:true}).waitFor()
    await page.getByText(texts.context,{exact:true}).first().waitFor();await page.getByText(texts.output,{exact:true}).waitFor()
   }
  }
  await feedback()
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
   await feedback(locale);assert.equal(requests.filter(request=>request.method==='PATCH').length,['menu-provider','settings-pending'].includes(view)?0:1)
  }
  assert.equal(requests.filter(request=>request.method==='POST').length,0)
  assert.deepEqual(errors,[],view);await page.close();console.log(view+': bilingual model presentation updates without repeating writes')
 }
},{entry:'/__model-save-entry.js',vite:{plugins:[{
 name:'model-save-language-fixture',resolveId(id){if(id==='/__model-save-entry.js')return id},load(id){if(id==='/__model-save-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__model-save',async(_req,res,next)=>{
  try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__model-save','<html><body><div id="root"></div><script type="module" src="/__model-save-entry.js"></script></body></html>'))}catch(error){next(error)}
 })},
}]}})

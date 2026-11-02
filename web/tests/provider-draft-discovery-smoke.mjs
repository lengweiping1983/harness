import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
import {mkdir} from 'node:fs/promises'
const entry=`import React from 'react';import {createRoot} from 'react-dom/client';import {ModelConnections} from '/src/components/overlays/ModelConnections.tsx';import {setLocale,t} from '/src/i18n/index.ts';import '/src/styles.css';window.locale=setLocale;window.copy=t;setLocale('en');createRoot(document.getElementById('root')).render(React.createElement(ModelConnections));`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage({viewport:{width:390,height:900}});page.setDefaultTimeout(15000)
 const errors=[];page.on('pageerror',error=>errors.push(error.message))
 let reads=0,probes=0,release,entered,catalogFallback=false
 const held=new Promise(resolve=>{entered=resolve})
 await page.route('**/api/v1/**',async route=>{
  const path=new URL(route.request().url()).pathname
  if(path==='/api/v1/connection'){reads++;return reads===1?route.fulfill({status:503,json:{error:'Fixture read failure'}}):route.fulfill({json:{version:2,revision:'fixture',providers:[],models:[],current:null,cycle:1}})}
  if(path==='/api/v1/connection/providers')return route.fulfill({json:{providers:[{id:'custom',name:'Custom API',base_url:'',api_key:true,oauth:false,env:[],models:[{id:'retired',name:'Retired model'},{id:'unselected-retired',name:'Unselected retired model',supported:false}]}]}})
  if(path==='/api/v1/codex-gateway')return route.fulfill({json:{accounts:[]}})
  if(path==='/api/v1/connection/discover'){
   probes++
   if(probes===1){entered();await new Promise(resolve=>{release=resolve});return route.fulfill({json:{models:[{id:'obsolete',name:'Obsolete draft model'}],fallback:false}}).catch(()=>{})}
   assert.equal(route.request().postDataJSON().connection.base_url,'https://new.example.invalid/v1')
   return route.fulfill({json:{models:[{id:'current',name:'Current draft model',contextWindow:131072,maxTokens:8192,input:['text','image']},{id:'another',name:'Another model'}],fallback:catalogFallback}})
  }
  errors.push(path);return route.fulfill({status:500,json:{error:'Unexpected fixture request'}})
 })
 await page.goto(server.resolvedUrls.local[0]+'__provider-draft')
 await page.getByRole('button',{name:'Retry reading',exact:true}).click();await page.getByText('No models configured. Choose a provider to get started.',{exact:true}).waitFor()
 await page.getByRole('button',{name:'Choose provider',exact:true}).click();await page.getByRole('button',{name:'Custom API',exact:true}).click()
 const endpoint=page.getByLabel('API endpoint address',{exact:true});await endpoint.fill('https://old.example.invalid/v1')
 await page.getByLabel('API key 1',{exact:true}).fill('fixture-secret')
 const picker=page.getByRole('dialog',{name:/Choose models/})
 const openPicker=async()=>{await page.getByRole('button',{name:/^Choose models \(/}).click();await picker.waitFor()}
 const closePicker=async confirm=>{await picker.getByRole('button',{name:confirm?'Confirm':'Cancel',exact:true}).click();await picker.waitFor({state:'detached'})}
 await openPicker();await picker.getByRole('checkbox',{name:'Retired model retired',exact:true}).check();await closePicker(true)
 await page.getByRole('button',{name:'Fetch available models',exact:true}).click();await held
 await endpoint.fill('https://new.example.invalid/v1');release()
 await page.getByRole('button',{name:'Fetch available models',exact:true}).click();await openPicker();await picker.getByText('Current draft model',{exact:true}).waitFor()
 assert.equal(await page.getByText('Obsolete draft model',{exact:true}).count(),0)
 await closePicker(false)
 assert.equal(probes,2)
 await page.getByText('Model list updated: 2 added, 2 retired; 1 removed from your selection. Save to apply.',{exact:true}).waitFor()
 await page.getByText('Model list: 2 total · 0 selected',{exact:true}).last().waitFor()
 await openPicker();await picker.getByRole('checkbox').first().check();await closePicker(true)
 await page.getByText('Model options',{exact:true}).click()
 const selected=page.getByRole('group',{name:/models selected|已选择.*个模型/})
 const limits=selected.locator('input[type=number]')
 assert.equal(await limits.nth(0).inputValue(),'131072')
 assert.equal(await limits.nth(1).inputValue(),'8192')
 // 手工添加表单也有同名能力勾选框，已选模型的能力必须限定在已选分组内核对。
 await selected.getByRole('checkbox',{name:'Image',exact:true}).isChecked().then(value=>assert.equal(value,true))
 await selected.getByRole('checkbox',{name:'Video',exact:true}).isChecked().then(value=>assert.equal(value,false))
 const modelId=page.getByRole('textbox',{name:'Enter a model ID',exact:true});await modelId.fill('https://misplaced.example.invalid')
 for(const language of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({language,theme})=>{window.locale(language);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{language,theme})
  const error=await page.evaluate(()=>window.copy('ui.providers.invalidModelId'));await page.getByText(error,{exact:true}).waitFor()
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false)
  await page.getByText(await page.evaluate(()=>window.copy('ui.providers.modelCounts',{total:2,selected:1})),{exact:true}).last().waitFor()
  await page.getByText(await page.evaluate(()=>window.copy('ui.providers.catalogUpdated',{added:2,retired:2,removed:1})),{exact:true}).waitFor()
  await page.getByText(await page.evaluate(()=>window.copy('ui.providers.metadataHint')),{exact:true}).waitFor()
  assert.equal(probes,2)
 }
 catalogFallback=true
 const discoverLabel=async()=>page.evaluate(()=>window.copy('ui.providers.discover'))
 await page.getByRole('button',{name:await discoverLabel(),exact:true}).click()
 for(const language of ['en','zh-CN','en']){
  await page.evaluate(value=>window.locale(value),language)
  const notice=await page.evaluate(()=>window.copy('ui.providers.discoverFallback'))
  await page.getByText(notice,{exact:true}).waitFor()
  assert.equal(await page.getByText(await page.evaluate(()=>window.copy('ui.providers.catalogUpdated',{added:2,retired:2,removed:1})),{exact:true}).count(),0)
 }
 catalogFallback=false;await page.evaluate(()=>window.locale('en'))
 await page.getByRole('button',{name:await discoverLabel(),exact:true}).click()
 await page.getByText(await page.evaluate(()=>window.copy('ui.providers.discoverFallback')),{exact:true}).waitFor({state:'detached'})
 await modelId.fill('');await page.evaluate(()=>{window.locale('zh-CN');document.documentElement.className='light';document.documentElement.dataset.theme='light'});
 await mkdir('../output',{recursive:true});await page.screenshot({path:'../output/model-configuration-optimized.png',fullPage:true})
 assert.deepEqual(errors,[]);await page.close()
 console.log('Provider drafts: retry loading, endpoint-bound discovery, stale rejection, catalog fallback notice, manual input validation and language/theme/width matrix passed')
},{entry:'/__provider-draft-entry.jsx',vite:{plugins:[{name:'provider-draft-fixture',resolveId(id){if(id==='/__provider-draft-entry.jsx')return id},load(id){if(id==='/__provider-draft-entry.jsx')return entry},configureServer(server){server.middlewares.use('/__provider-draft',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__provider-draft','<html><body><div id="root"></div><script type="module" src="/__provider-draft-entry.jsx"></script></body></html>'))}catch(error){next(error)}})}}]}})

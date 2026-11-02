import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import {createServer} from 'node:http'
import {createInterface} from 'node:readline'
import {fileURLToPath} from 'node:url'
import {mkdir} from 'node:fs/promises'
import {browserScenario} from './browser-scenario.mjs'
import {fixturePython} from './python-fixture.mjs'
import {cleanupFixture} from './fixture-cleanup.mjs'
const root=fileURLToPath(new URL('../../',import.meta.url))
const backend=spawn(fixturePython,['-m','tests.web_fixture'],{cwd:root,stdio:['ignore','pipe','pipe']})
let listing=[], releaseCatalog, enteredCatalog;const catalogEntered=new Promise(resolve=>{enteredCatalog=resolve});const upstream=createServer(async(request,response)=>{if(!releaseCatalog){enteredCatalog();await new Promise(resolve=>{releaseCatalog=resolve})}response.setHeader('Content-Type','application/json');response.end(JSON.stringify({data:listing}))});
await new Promise(resolve=>upstream.listen(0,'127.0.0.1',resolve));const endpoint='http://127.0.0.1:'+upstream.address().port+'/v1'
const lines=createInterface({input:backend.stdout});let diagnostics='',timer,failure
backend.stderr.on('data',data=>{diagnostics+=data})
const entry=`import React from 'react';import {createRoot} from 'react-dom/client';import {ModelConnections} from '/src/components/overlays/ModelConnections.tsx';import {latestProviderModels} from '/src/lib/providerConnections.ts';import {setLocale,t} from '/src/i18n/index.ts';import '/src/styles.css';setLocale('en');window.locale=setLocale;window.copy=t;window.defaults=models=>latestProviderModels(models).map(model=>model.id);createRoot(document.getElementById('root')).render(React.createElement(ModelConnections));`
try{
 const ready=await new Promise((resolve,reject)=>{timer=setTimeout(()=>reject(Error('Fixture startup timed out: '+diagnostics)),20000);backend.once('error',reject);backend.once('exit',code=>reject(Error('Fixture exited '+code+': '+diagnostics)));lines.on('line',line=>{try{const value=JSON.parse(line);if(Number.isInteger(value.port))resolve(value)}catch{}})})
 clearTimeout(timer)
 const base='http://127.0.0.1:'+ready.port
 const get=async path=>{const response=await fetch(base+path);assert.equal(response.status,200);return response.json()}
 const template=(await get('/api/v1/connection/providers')).providers.find(provider=>provider.id==='openai')
 assert.ok(template.models.length>1)
 await browserScenario(async({server,browser})=>{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(15000)
  const picker=()=>page.getByRole('dialog',{name:/Choose models/})
  const openPicker=async()=>{await page.getByRole('button',{name:/^Choose models \(/}).click();await picker().waitFor()}
  const closePicker=async confirm=>{await picker().getByRole('button',{name:confirm?'Confirm':'Cancel',exact:true}).click();await picker().waitFor({state:'detached'})}
  const errors=[];let probes=0,writes=0;page.on('pageerror',error=>errors.push(error.message))
  await page.route('**/api/v1/**',async route=>{
   const request=route.request(),url=new URL(request.url());if(request.method()==='PATCH'&&url.pathname==='/api/v1/connection')writes++
   if(url.pathname==='/api/v1/connection/discover')probes++
   const response=await fetch(base+url.pathname+url.search,{method:request.method(),headers:{'Content-Type':'application/json','Accept-Language':'en'},...(request.postData()?{body:request.postData()}: {})})
   await route.fulfill({status:response.status,contentType:'application/json',body:await response.text()})
  })
  await page.goto(server.resolvedUrls.local[0]+'__provider-native')
  await page.getByRole('button',{name:'Choose provider',exact:true}).click();await page.getByRole('textbox',{name:'Search providers',exact:true}).fill('openai');await page.getByRole('button',{name:'OpenAI',exact:true}).click()
  await page.getByRole('button',{name:/^Choose models \(/}).waitFor()
  await openPicker()
  assert.equal(await picker().getByRole('checkbox').count(),Math.min(50,template.models.length))
  assert.equal(await picker().getByRole('checkbox').evaluateAll(rows=>rows.filter(row=>row.checked).length),0)
  await picker().getByRole('button',{name:'Add latest per series',exact:true}).click()
  const defaults=await page.evaluate(models=>window.defaults(models),template.models);assert.ok(defaults.length<template.models.length)
  await closePicker(true)
  await page.getByText(`Model list: ${template.models.length} total · ${defaults.length} selected`,{exact:true}).last().waitFor()
  assert.equal(await page.getByRole('button',{name:'Add another endpoint',exact:true}).isVisible(),false)
  await page.getByRole('button',{name:'Fetch available models',exact:true}).click()
  await page.getByRole('alert').filter({hasText:'Enter an API key and try again.'}).waitFor();assert.equal(probes,0)
  await page.getByRole('button',{name:'Save',exact:true}).click();assert.equal(writes,0)
  await mkdir('../output/provider-native',{recursive:true})
  for(const language of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:1000});await page.evaluate(({language,theme})=>{window.locale(language);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{language,theme})
   const copy=await page.evaluate(()=>({error:window.copy('ui.providers.keyRequired'),description:window.copy('ui.providers.description')}))
   await page.getByRole('alert').first().waitFor();assert.ok((await page.getByRole('alert').allTextContents()).every(value=>value===copy.error));await page.getByText(copy.description,{exact:true}).waitFor()
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false)
   if(language==='zh-CN'&&width===1440){await page.getByLabel('提供商名称',{exact:true}).scrollIntoViewIfNeeded();await page.screenshot({path:`../output/provider-native/validation-${theme}.png`})}
  }
  await page.getByLabel('API key 1',{exact:true}).fill('isolated-fixture-only-key')
  await page.getByLabel('API endpoint address',{exact:true}).fill(endpoint)
  await page.getByRole('button',{name:'Save',exact:true}).click();await page.getByRole('button',{name:'Edit',exact:true}).click();assert.equal(writes,1)
  const initial=(await get('/api/v1/connection')).providers[0].connections[0]
  assert.deepEqual(initial.models.map(model=>model.model),defaults)
  const retired=defaults[0];listing=[...template.models.filter(model=>model.id!==retired),{id:'fresh-reasoner-2',name:'Fresh reasoner',reasoning:true,thinkingLevelMap:{low:'low',high:'high',max:'max'},contextWindow:128000,maxTokens:8192}]
  await page.getByRole('button',{name:'Fetch available models',exact:true}).click();await catalogEntered;assert.equal(await page.getByRole('button',{name:'Save',exact:true}).isDisabled(),true);await page.getByLabel('Provider name',{exact:true}).fill('OpenAI reviewed');await page.getByText('Model options',{exact:true}).nth(1).click();await page.getByRole('group',{name:/models selected/}).getByRole('textbox',{name:'Model name',exact:true}).fill('Keep model edit');releaseCatalog();await page.getByRole('alert').waitFor({state:'detached'});assert.equal(probes,1)
  await page.getByText(`Model list: ${listing.length} total · ${defaults.length-1} selected`,{exact:true}).last().waitFor()
  await openPicker();await picker().getByRole('textbox',{name:'Search models',exact:true}).fill('fresh-reasoner-2');await picker().getByText('Fresh reasoner',{exact:true}).waitFor();assert.equal(await picker().getByRole('checkbox').count(),1)
  assert.equal(await page.getByText(retired,{exact:true}).count(),0)
  await page.getByText('Keep model edit',{exact:true}).waitFor();assert.equal(await page.getByLabel('Provider name',{exact:true}).inputValue(),'OpenAI reviewed');await picker().getByRole('checkbox',{name:/Fresh reasoner/}).check()
  await closePicker(true)
  if(defaults.length>10)await page.getByRole('textbox',{name:'Search selected models',exact:true}).fill('fresh-reasoner-2');await page.getByText('Model options',{exact:true}).last().click();assert.equal(await page.getByLabel('Reasoning levels',{exact:true}).last().inputValue(),'custom')
  await page.getByRole('button',{name:'Save',exact:true}).click();await page.getByRole('button',{name:'Edit',exact:true}).waitFor();assert.equal(writes,2)
  const saved=(await get('/api/v1/connection')).providers.find(provider=>provider.type==='openai').connections[0]
  assert.deepEqual(saved.models.map(model=>model.model),[...defaults.filter(id=>id!==retired),'fresh-reasoner-2']);assert.equal(saved.model_catalog.length,listing.length);assert.equal(saved.model_catalog.some(model=>model.id===retired),false);
  const published=(await get('/api/v1/models')).models.find(model=>model.label==='Fresh reasoner');assert.equal(published.supports_reasoning,true);assert.deepEqual(published.reasoning_efforts,['low','high','max']);assert.equal(saved.keys[0].mask,'******-key');assert.equal(saved.keys[0].value,undefined)
  await page.reload();await page.getByRole('button',{name:'Edit',exact:true}).click();await page.getByPlaceholder('******-key',{exact:true}).waitFor()
  await openPicker();assert.equal(await picker().getByRole('checkbox').count(),Math.min(50,listing.length));assert.equal(await page.getByText(retired,{exact:true}).count(),0);await closePicker(false)
  await page.getByRole('button',{name:'Save',exact:true}).click();await page.getByRole('button',{name:'Edit',exact:true}).waitFor();assert.equal(writes,3)
  assert.deepEqual((await get('/api/v1/connection')).providers[0].connections[0].models.map(model=>model.id),saved.models.map(model=>model.id));assert.deepEqual(errors,[])
  await page.close();console.log('Real isolated backend: empty defaults and explicit latest selection, complete refreshed catalog replaces cache, retired saved choices removed, fresh reasoning levels, persistent cache, actionable bilingual credential checks without requests, progressive endpoint controls, discovery, save/readback and masked edit roundtrip passed')
 },{entry:'/__provider-native-entry.jsx',vite:{plugins:[{name:'provider-native-fixture',resolveId(id){if(id==='/__provider-native-entry.jsx')return id},load(id){if(id==='/__provider-native-entry.jsx')return entry},configureServer(server){server.middlewares.use('/__provider-native',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__provider-native','<html><body><div id="root"></div><script type="module" src="/__provider-native-entry.jsx"></script></body></html>'))}catch(error){next(error)}})}}]}})
}catch(error){failure=error}finally{clearTimeout(timer);releaseCatalog?.();await new Promise(resolve=>upstream.close(resolve));await cleanupFixture({error:failure,lines,backend})}
